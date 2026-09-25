"""
LangGraph DAG: Alpha → quality edge → Beta → Gamma → policy edge → Delta → END.
Pattern and isolated_failure_domain are contracts, not decoration.
"""

from typing import TypedDict, Optional, Dict, Any, List
from datetime import datetime, timezone
from uuid import uuid4

from langgraph.graph import StateGraph, END

from src.config import settings
from src.schemas.telemetry import TelemetryFrame
from src.schemas.predictions import (
    PredictRULResponse,
    CableCheckStatus,
    DefectLocalization,
    RULPrediction,
    ThermalDeWeathering,
    ElectricalHealth,
    CMMSWorkOrder,
)
from src.agents.agent_alpha import AgentAlpha
from src.agents.agent_beta import AgentBeta
from src.agents.agent_gamma import AgentGamma
from src.agents.agent_delta import AgentDelta, CONDITION_PM_PATTERNS
from src.models.iso_20816 import classify_velocity_zone, evaluation_velocity_rms, normalize_group, normalize_support, zone_health_score
from src.models.live_cues import is_electrically_loaded, is_operating, max_thd_pct
from src.services.event_historian import event_historian
from src.utils.logger import get_logger

logger = get_logger("Agents.Orchestrator")


class APMSAgentState(TypedDict):
    frame: TelemetryFrame
    cable_check: Optional[CableCheckStatus]
    thermal_de_weathering: Optional[ThermalDeWeathering]
    electrical_health: Optional[ElectricalHealth]
    defect_localization: Optional[DefectLocalization]
    rul_prediction: Optional[RULPrediction]
    cmms_work_order: Optional[CMMSWorkOrder]
    overall_health_score: float
    overall_health_status: str
    iso_vibration_zone: Optional[str]
    iso_machine_group: Optional[str]
    iso_support: Optional[str]
    skip_reason: Optional[str]
    error_message: Optional[str]


class APMSOrchestrator:
    def __init__(self):
        self.alpha = AgentAlpha()
        self.beta = AgentBeta()
        self.gamma = AgentGamma()
        self.delta = AgentDelta()
        self.workflow = self._build_graph()

    def reset_machine(self, machine_id: str) -> str:
        from src.services.oem_mailer import note_machine_cleared
        from src.services.alert_dispatcher import note_alert_cleared
        self.delta.wo_tracker.clear_open_work_order(machine_id)
        note_machine_cleared(machine_id)
        note_alert_cleared(machine_id)
        return self.alpha.reset_buffer(machine_id)

    def _node_agent_alpha(self, state: APMSAgentState) -> Dict[str, Any]:
        return {"cable_check": self.alpha.process(state["frame"])}

    def _node_agent_beta(self, state: APMSAgentState) -> Dict[str, Any]:
        thermal_out, electrical_out = self.beta.process(state["frame"], state.get("cable_check"))
        return {
            "thermal_de_weathering": thermal_out,
            "electrical_health": electrical_out,
        }

    def _node_agent_gamma(self, state: APMSAgentState) -> Dict[str, Any]:
        frame = state["frame"]
        history: List[Dict[str, Any]] = []
        try:
            from src.database.telemetry_store import list_agent_snapshots
            history = list_agent_snapshots(frame.machine_id, limit=48)
        except Exception:
            history = []
        defect_out, rul_out = self.gamma.process(
            frame,
            cable_check=state.get("cable_check"),
            thermal=state.get("thermal_de_weathering"),
            electrical=state.get("electrical_health"),
            history=history,
        )
        electrical = state.get("electrical_health")
        group = normalize_group(getattr(frame, "iso_machine_group", None))
        support = normalize_support(getattr(frame, "iso_support", None))
        elec_penalty = 0.0
        if electrical and electrical.isolated_failure_domain == "ELECTRICAL":
            elec_penalty = min(25.0, electrical.voltage_unbalance_pct * 6.0)
        thd = max_thd_pct(frame)
        if thd > settings.THD_NORMAL_MAX:
            elec_penalty += min(12.0, (thd - settings.THD_NORMAL_MAX) * 1.5)
        if is_electrically_loaded(frame) and frame.em_power_factor is not None and float(frame.em_power_factor) < settings.PF_NORMAL_MIN:
            elec_penalty += 8.0
        freq = float(frame.em_frequency or 0.0)
        if freq > 0.0 and (freq < settings.FREQ_NORMAL_MIN or freq > settings.FREQ_NORMAL_MAX):
            elec_penalty += 4.0
        eval_rms = evaluation_velocity_rms(frame)
        has_rms = frame.field_was_sent("imuAcceleration") or frame.has_triaxial_axes()
        if has_rms:
            health_score = zone_health_score(
                eval_rms,
                group=group,
                support=support,
                electrical_penalty=elec_penalty,
            )
            iso_zone = classify_velocity_zone(eval_rms, group, support)
        else:
            health_score = round(max(5.0, 100.0 - elec_penalty), 1)
            iso_zone = None
        return {
            "defect_localization": defect_out,
            "rul_prediction": rul_out,
            "overall_health_score": health_score,
            "iso_vibration_zone": iso_zone,
            "iso_machine_group": group,
            "iso_support": support,
        }

    def _node_agent_delta(self, state: APMSAgentState) -> Dict[str, Any]:
        defect = state.get("defect_localization")
        rul = state.get("rul_prediction")
        if not defect or not rul:
            return {"cmms_work_order": None, "skip_reason": "POLICY"}
        cmms_out = self.delta.process(
            state["frame"],
            defect,
            rul,
            cable_check=state.get("cable_check"),
            thermal=state.get("thermal_de_weathering"),
            electrical=state.get("electrical_health"),
        )
        if cmms_out is None:
            return {"cmms_work_order": None, "skip_reason": "HEALTHY"}
        if getattr(cmms_out, "is_duplicate", False):
            return {"cmms_work_order": cmms_out, "skip_reason": "OPEN_WO"}
        return {"cmms_work_order": cmms_out, "skip_reason": None}

    def _node_skip_delta(self, state: APMSAgentState) -> Dict[str, Any]:
        logger.info("[Orchestrator] Policy skip — Delta not executed.")
        return {"cmms_work_order": None, "skip_reason": "HEALTHY"}

    def _check_signal_quality(self, state: APMSAgentState) -> str:
        cable_check = state.get("cable_check")
        if cable_check and cable_check.status != "VALID":
            logger.warning(f"[Orchestrator] Signal quality {cable_check.status} — halt DAG, persist SENSOR event.")
            return "halt"
        return "continue"

    def _should_create_work_order(self, state: APMSAgentState) -> str:
        defect = state.get("defect_localization")
        rul = state.get("rul_prediction")
        cable = state.get("cable_check")
        thermal = state.get("thermal_de_weathering")
        pattern = cable.pattern_recognition_status if cable else "STABLE"
        condition_pm = pattern in CONDITION_PM_PATTERNS or bool(
            thermal and (thermal.genuine_thermal_overheating or thermal.pm_hint)
        )
        if not defect or not rul:
            return "skip"
        if getattr(defect, "diagnosis_method", "") == "imu_absent":
            return "skip"
        if defect.defect_code == "NORMAL" and not condition_pm:
            return "skip"
        return "create"

    def _build_graph(self):
        graph = StateGraph(APMSAgentState)
        graph.add_node("agent_alpha", self._node_agent_alpha)
        graph.add_node("agent_beta", self._node_agent_beta)
        graph.add_node("agent_gamma", self._node_agent_gamma)
        graph.add_node("agent_delta", self._node_agent_delta)
        graph.add_node("skip_delta", self._node_skip_delta)
        graph.set_entry_point("agent_alpha")
        graph.add_conditional_edges(
            "agent_alpha",
            self._check_signal_quality,
            {"halt": END, "continue": "agent_beta"},
        )
        graph.add_edge("agent_beta", "agent_gamma")
        graph.add_conditional_edges(
            "agent_gamma",
            self._should_create_work_order,
            {"skip": "skip_delta", "create": "agent_delta"},
        )
        graph.add_edge("agent_delta", END)
        graph.add_edge("skip_delta", END)
        return graph.compile()

    def _derive_status_and_card(self, state: dict) -> tuple:
        cable = state.get("cable_check")
        if cable and cable.status != "VALID":
            return "SENSOR_FAULT", "SENSOR_FAULT"
        defect = state.get("defect_localization")
        rul = state.get("rul_prediction")
        wo = state.get("cmms_work_order")
        if wo and wo.ticket_type == "PM_CONDITION":
            return "WATCH", "PM_DUE"
        if defect and defect.defect_code != "NORMAL":
            if rul and rul.rul_days <= 14:
                return "CRITICAL", "MACHINE_RISK"
            return "DEGRADED", "MACHINE_RISK"
        if state.get("overall_health_score", 100) < 70:
            return "WATCH", "MONITOR"
        return "HEALTHY", "MONITOR"

    def run(self, frame: TelemetryFrame, *, notify_oem: bool = True) -> PredictRULResponse:
        trace_id = f"trc_apms_{uuid4().hex[:12]}"
        initial_state: APMSAgentState = {
            "frame": frame,
            "cable_check": None,
            "thermal_de_weathering": None,
            "electrical_health": None,
            "defect_localization": None,
            "rul_prediction": None,
            "cmms_work_order": None,
            "overall_health_score": 100.0,
            "overall_health_status": "HEALTHY",
            "iso_vibration_zone": None,
            "iso_machine_group": None,
            "iso_support": None,
            "skip_reason": None,
            "error_message": None,
        }
        previous_notify = self.delta.notify_oem
        self.delta.notify_oem = notify_oem
        try:
            final_state = self.workflow.invoke(initial_state)
        finally:
            self.delta.notify_oem = previous_notify
        cable = final_state.get("cable_check") or CableCheckStatus(
            status="VALID", namur_status="GOOD", namur_ne43_signal_valid=True, alert_suppressed=False, fault_reason=None
        )
        cable.trace_id = trace_id

        # Persist halt event to historian if Agent Alpha halted DAG
        if cable.status != "VALID":
            event_historian.persist_halt_event(trace_id, frame.machine_id, cable)

        health_status, card_type = self._derive_status_and_card(final_state)
        score = 5.0 if health_status == "SENSOR_FAULT" else final_state.get("overall_health_score", 100.0)

        response = PredictRULResponse(
            machine_id=frame.machine_id,
            trace_id=trace_id,
            timestamp=datetime.now(timezone.utc),
            overall_health_score=score,
            overall_health_status=health_status,
            iso_vibration_zone=final_state.get("iso_vibration_zone"),
            iso_machine_group=final_state.get("iso_machine_group"),
            iso_support=final_state.get("iso_support"),
            card_type=card_type,
            skip_reason=final_state.get("skip_reason"),
            cable_check=cable,
            defect_localization=final_state.get("defect_localization"),
            rul_prediction=final_state.get("rul_prediction"),
            thermal_de_weathering=final_state.get("thermal_de_weathering"),
            electrical_health=final_state.get("electrical_health"),
            cmms_work_order=final_state.get("cmms_work_order"),
        )

        # Persist completed diagnosis run to historian and Postgres agent_snapshot.
        event_historian.persist_diagnosis_run(trace_id, frame.machine_id, response)
        try:
            from src.database.telemetry_store import persist_agent_snapshot
            persist_agent_snapshot(frame, response)
        except Exception as exc:
            logger.debug("[Orchestrator] agent_snapshot persist skipped: %s", exc)
        return response
