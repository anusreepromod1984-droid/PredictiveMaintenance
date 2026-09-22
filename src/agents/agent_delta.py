"""
Agent Delta: CMMS advisory — PdM vs condition PM, skip policy, no invented SAP ids.
"""

from typing import Optional
from datetime import datetime, timezone

from src.config import settings
from src.schemas.telemetry import TelemetryFrame
from src.schemas.predictions import (
    CableCheckStatus,
    DefectLocalization,
    RULPrediction,
    ThermalDeWeathering,
    ElectricalHealth,
    CMMSWorkOrder,
    OemMailDraft,
)
from src.models.supplier_sourcing_engine import SupplierSourcingEngine
from src.models.fault_bom import resolve_bom
from src.models.demo_crm import demo_sap_order_id, is_demo_crm_connected
from src.models.oem_mail import compose_oem_mail
from src.models.shift_calendar import next_repair_window
from src.agents.open_wo_tracker import wo_tracker, OpenWorkOrderTracker
from src.services.oem_mailer import dispatch_if_needed
from src.utils.logger import get_logger

logger = get_logger("Agents.Delta")

CONDITION_PM_PATTERNS = {"NOISE_EXPANSION", "STEADY_CLIMB", "STUCK_HIGH", "SUDDEN_JUMP_FLAT", "REPEATED_SPIKES"}


class AgentDelta:
    def __init__(self, tracker: Optional[OpenWorkOrderTracker] = None):
        self.sourcing_engine = SupplierSourcingEngine()
        self.wo_tracker = tracker or wo_tracker
        # MQTT background ticks set this False so live leak/misalignment flips
        # cannot spam the inbox. Diagnose (predict_rul) leaves it True.
        self.notify_oem = True

    def process(
        self,
        frame: TelemetryFrame,
        defect: DefectLocalization,
        rul: RULPrediction,
        cable_check: Optional[CableCheckStatus] = None,
        thermal: Optional[ThermalDeWeathering] = None,
        electrical: Optional[ElectricalHealth] = None,
    ) -> Optional[CMMSWorkOrder]:
        pattern = cable_check.pattern_recognition_status if cable_check else "STABLE"
        genuine_heat = bool(thermal and thermal.genuine_thermal_overheating)
        pm_hint = thermal.pm_hint if thermal else None

        condition_pm = pattern in CONDITION_PM_PATTERNS or genuine_heat or bool(pm_hint)
        imu_skip = (defect.diagnosis_method or "") == "imu_absent" or (
            defect.defect_code == "NORMAL" and "vibration prediction skipped" in (defect.defect_name or "").lower()
        )

        if imu_skip:
            logger.info(f"[Agent Delta] Skip WO for {frame.machine_id}: IMU absent / vibration prediction skipped.")
            return None

        if defect.defect_code == "NORMAL" and not condition_pm:
            logger.info(f"[Agent Delta] Skip WO for {frame.machine_id}: HEALTHY (NORMAL, RUL {rul.rul_days}d).")
            return None

        if defect.defect_code in {"PF001"}:
            ticket_type = "PM_CONDITION"
        elif condition_pm and (defect.defect_code == "NORMAL" or rul.rul_days > settings.RUL_SKIP_THRESHOLD_DAYS):
            ticket_type = "PM_CONDITION"
        else:
            ticket_type = "PDM_CORRECTIVE"

        bom = resolve_bom(defect.defect_code, defect.failing_component, frame.bearing_model)
        part_query = bom.part_key or defect.reasoned_part_key or defect.failing_component or frame.bearing_model
        source_code = defect.defect_code if bom.part_key else (None if defect.reasoned_part_key else defect.defect_code)
        sourcing_intel = self.sourcing_engine.evaluate_sourcing(
            component_query=part_query,
            rul_days=rul.rul_days,
            defect_code=source_code,
        )

        slot = next_repair_window(rul.rul_days, ticket_type)
        pm_due = rul.recommended_repair_by_date

        # Keep the same ticket id if one is already open, but always refresh buy links.
        existing_wo = self.wo_tracker.has_open_work_order(frame.machine_id, defect.defect_code)
        if existing_wo:
            logger.info(
                f"[Agent Delta] Duplicate suppressed for {frame.machine_id}: "
                f"Open WO '{existing_wo.get('work_order_id')}' already covers defect '{defect.defect_code}'."
            )
            if is_demo_crm_connected():
                existing_wo["work_order_id"] = demo_sap_order_id(frame.machine_id, defect.defect_code)
                sourcing_intel.crm_status = "SAP_PM_OPEN"
            if sourcing_intel.ladder:
                order_id = existing_wo.get("work_order_id") or demo_sap_order_id(frame.machine_id, defect.defect_code)
                sourcing_intel.ladder[0].detail = f"SAP {order_id}" if is_demo_crm_connected() else sourcing_intel.ladder[0].detail
            existing_wo["sourcing_intelligence"] = sourcing_intel.model_dump(mode="json")
            existing_wo["reserved_spare_part_bom"] = (
                f"{sourcing_intel.oem_part_number} (buy options attached — ERP ATP not reserved)"
                if sourcing_intel.purchase_options
                else existing_wo.get("reserved_spare_part_bom", frame.bearing_model)
            )
            existing_wo["scheduled_repair_window"] = slot["label"]
            existing_wo["scheduled_repair_at"] = slot["start"]
            existing_wo["scheduled_repair_end"] = slot["end"]
            existing_wo["pm_due_date"] = pm_due
            existing_wo["repair_crew"] = slot["crew"]
            wo = CMMSWorkOrder(
                work_order_id=existing_wo.get("work_order_id", f"ADVISORY-OPEN-{frame.machine_id}"),
                ticket_type=existing_wo.get("ticket_type", ticket_type),
                is_advisory_draft=not is_demo_crm_connected(),
                is_duplicate=True,
                scheduled_repair_window=slot["label"],
                scheduled_repair_at=slot["start"],
                scheduled_repair_end=slot["end"],
                pm_due_date=pm_due,
                repair_crew=slot["crew"],
                reserved_spare_part_bom=existing_wo.get("reserved_spare_part_bom", frame.bearing_model),
                reserved_warehouse_bin=existing_wo.get("reserved_warehouse_bin", "UNCONFIRMED_ERP"),
                sourcing_intelligence=sourcing_intel,
            )
            prior = existing_wo.get("oem_mail") if isinstance(existing_wo.get("oem_mail"), dict) else {}
            wo.oem_mail = self._attach_mail(frame.machine_id, defect, rul, wo, prior)
            if wo.oem_mail:
                existing_wo["oem_mail"] = wo.oem_mail.model_dump(mode="json")
            self.wo_tracker.register_open_work_order(frame.machine_id, defect.defect_code, existing_wo)
            return wo

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        demo_crm = is_demo_crm_connected()
        if demo_crm:
            work_order_id = demo_sap_order_id(frame.machine_id, defect.defect_code)
        else:
            work_order_id = f"ADVISORY-DRAFT-{frame.machine_id}-{stamp}"
        window = slot["label"]
        part_label = sourcing_intel.oem_part_number or bom.part_label
        rec = sourcing_intel.sourcing_recommendation
        if rec == "RESERVE_FROM_STORES":
            spare = f"{part_label} ×1"
            bin_id = sourcing_intel.stores_bin or "STORES"
        elif rec == "TAGGED_VENDOR_ORDER":
            spare = f"{part_label} — order from {sourcing_intel.tagged_vendor_name}"
            bin_id = f"VENDOR:{sourcing_intel.tagged_vendor_name}"
        elif rec == "NO_BOM_SPARE":
            spare = bom.part_label
            bin_id = "NO_BOM"
        else:
            spare = f"{part_label} (buy options attached — ERP ATP not reserved)"
            bin_id = "UNCONFIRMED_ERP"

        if sourcing_intel.ladder:
            sourcing_intel.ladder[0].detail = (
                f"SAP {work_order_id}" if demo_crm else sourcing_intel.ladder[0].detail
            )
        if demo_crm:
            sourcing_intel.crm_status = "SAP_PM_OPEN"

        logger.info(
            f"[Agent Delta] {ticket_type} advisory {work_order_id} pattern={pattern} "
            f"sourcing={rec} source={sourcing_intel.winning_source}"
        )

        wo = CMMSWorkOrder(
            work_order_id=work_order_id,
            ticket_type=ticket_type,
            is_advisory_draft=not demo_crm,
            is_duplicate=False,
            scheduled_repair_window=window,
            scheduled_repair_at=slot["start"],
            scheduled_repair_end=slot["end"],
            pm_due_date=pm_due,
            repair_crew=slot["crew"],
            reserved_spare_part_bom=spare,
            reserved_warehouse_bin=bin_id,
            sourcing_intelligence=sourcing_intel,
        )
        record = wo.model_dump(mode="json")
        self.wo_tracker.register_open_work_order(frame.machine_id, defect.defect_code, record)
        wo.oem_mail = self._attach_mail(frame.machine_id, defect, rul, wo, None)
        if wo.oem_mail:
            record["oem_mail"] = wo.oem_mail.model_dump(mode="json")
            self.wo_tracker.register_open_work_order(frame.machine_id, defect.defect_code, record)
        return wo

    def _attach_mail(self, machine_id, defect, rul, wo, prior):
        prior = prior if isinstance(prior, dict) else {}
        if defect.defect_code in {"NORMAL", "NONE", "SENSOR", "HARDWARE_CABLE_FAULT", "EF001"}:
            return None
        if not self.notify_oem:
            if prior.get("subject"):
                try:
                    return OemMailDraft.model_validate(prior)
                except Exception:
                    return None
            return compose_oem_mail(machine_id, defect, rul, wo)
        draft = compose_oem_mail(machine_id, defect, rul, wo)
        return dispatch_if_needed(
            draft,
            machine_id=machine_id,
            defect_code=defect.defect_code,
            prior=prior,
        )
