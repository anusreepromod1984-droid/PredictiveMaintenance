"""
FastAPI Routes for Industrial Multi-Channel Alerts (WhatsApp & Email)
Endpoints to inspect status, view alert history, trigger tests, and manage cooldown states.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.config import settings
from src.schemas.predictions import (
    PredictRULResponse,
    CableCheckStatus,
    DefectLocalization,
    RULPrediction,
    MasterMaintenanceGuidance,
    RepairOption,
)
from src.schemas.telemetry import TelemetryFrame
from src.services.alert_dispatcher import (
    dispatch_alert_notifications,
    get_alert_history,
    note_alert_cleared,
    send_email_alert,
    send_whatsapp_alert,
)
from src.utils.logger import get_logger

logger = get_logger("API.Alerts")
router = APIRouter(prefix="/api/v1", tags=["Alert Notifications"])


class TestAlertRequest(BaseModel):
    machine_id: str = Field(default="compressor_unit_01", description="Asset machine ID")
    severity: str = Field(default="CRITICAL", description="Severity level: CRITICAL, SEVERE, WARNING")
    defect_name: str = Field(default="Bearing Inner Race Defect (BPFI)", description="Defect description")
    defect_code: str = Field(default="BPFI", description="Defect code (BPFI, MF001, MF002, etc.)")
    whatsapp_recipient: Optional[str] = Field(default=None, description="Optional override WhatsApp phone number")
    email_recipient: Optional[str] = Field(default=None, description="Optional override email address")
    custom_note: Optional[str] = Field(default="Manual test trigger from APMS Operations Console", description="Custom note")


class ClearCooldownRequest(BaseModel):
    machine_id: str = Field(..., description="Asset machine ID to clear debounce cooldown")


@router.get(
    "/alerts/status",
    summary="Get Multi-Channel Alert Configuration Status",
)
async def get_alert_status():
    """Returns current configuration and operational status of WhatsApp and Email alerts."""
    wa_token_set = bool((settings.WHATSAPP_TOKEN or "").strip())
    wa_phone_id = settings.WHATSAPP_PHONE_NUMBER_ID or ""
    wa_to = settings.WHATSAPP_ALERT_TO or ""
    mail_url = settings.OEM_MAIL_API_URL or ""
    mail_to = getattr(settings, "ALERT_MAIL_TO", "") or settings.OEM_MAIL_TO or ""

    return {
        "notifications_enabled": getattr(settings, "ALERT_NOTIFICATION_ENABLED", True),
        "min_severity": getattr(settings, "ALERT_MIN_SEVERITY", "WARNING"),
        "cooldown_seconds": getattr(settings, "ALERT_NOTIFICATION_COOLDOWN_SECONDS", 1800),
        "whatsapp": {
            "configured": bool(wa_token_set and wa_phone_id and wa_to),
            "phone_number_id": wa_phone_id,
            "recipients": [p.strip() for p in wa_to.split(",") if p.strip()],
            "api_version": getattr(settings, "WHATSAPP_API_VERSION", "v18.0"),
        },
        "email": {
            "configured": bool(mail_url and mail_to),
            "gateway_url": mail_url,
            "recipient": mail_to,
            "sp_client_id": settings.OEM_MAIL_SP_CLIENT_ID or "",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get(
    "/alerts/history",
    summary="Get Recent Dispatched Alert Audit Trail",
    response_model=List[Dict[str, Any]],
)
async def get_alerts_history(limit: int = 50):
    """Returns the most recent dispatched alert events (WhatsApp & Email)."""
    return get_alert_history(limit=limit)


@router.post(
    "/alerts/test",
    summary="Trigger Manual / Test Alert to WhatsApp and Email",
)
async def trigger_test_alert(req: TestAlertRequest):
    """
    Constructs an industrial alert with the specified severity and dispatches
    it immediately via WhatsApp Business API and the Splus Email Gateway.
    """
    machine_id = req.machine_id.strip()
    sev = req.severity.upper()

    rul_days = 3.0 if sev == "CRITICAL" else (18.0 if sev == "SEVERE" else 45.0)
    iso_zone = "D" if sev == "CRITICAL" else ("C" if sev == "SEVERE" else "B")
    status_label = "CRITICAL" if sev == "CRITICAL" else ("DEGRADED" if sev == "SEVERE" else "WATCH")

    repair = RepairOption(
        option_title=f"Repair for {req.defect_name}",
        action_type="IMMEDIATE_TRIAGE" if sev == "CRITICAL" else "PLANNED_OVERHAUL",
        estimated_duration_hours=4.0,
        step_by_step_instructions=[
            f"Isolate machine {machine_id} per plant safety lockout procedures.",
            f"Inspect {req.defect_name} ({req.defect_code}) and check mechanical tolerances.",
            "Replace defective subassembly and re-align using precision laser kit.",
            "Perform post-repair baseline vibration check (< 1.20 mm/s target).",
        ],
        required_tools_and_materials=["Precision dial indicator", "Torque wrench", "Replacement assembly"],
        safety_precautions=["LOTO isolation mandatory", "Wear PPE gloves and goggles"],
    )

    guidance = MasterMaintenanceGuidance(
        defect_code=req.defect_code,
        component_exact_location="Main Drive Assembly",
        root_cause_mechanism="Accelerated mechanical wear identified by AI diagnostic engine.",
        severity_level=sev,
        immediate_field_triage="Immediate technician dispatch recommended to prevent catastrophic failure.",
        planned_overhaul_playbook=repair,
        expert_tips=["Check lubricant contamination level.", "Verify bolt torque to specification."],
    )

    defect = DefectLocalization(
        defect_code=req.defect_code,
        defect_name=req.defect_name,
        failing_component="Drive End Bearing / Coupler",
        confidence_percentage=92.5,
        dominant_frequencies_hz=[118.4, 236.8],
        expert_repair_guidance=guidance,
    )

    rul = RULPrediction(
        rul_operating_hours=rul_days * 24.0,
        rul_days=rul_days,
        confidence_interval_bounds=f"B10: {int(rul_days*18)}h, B50: {int(rul_days*24)}h",
        recommended_repair_by_date=(datetime.now(timezone.utc)).strftime("%Y-%m-%d"),
    )

    cable = CableCheckStatus(status="VALID", alert_suppressed=False)

    pred = PredictRULResponse(
        machine_id=machine_id,
        trace_id=f"test_trc_{datetime.now(timezone.utc).strftime('%H%M%S')}",
        timestamp=datetime.now(timezone.utc),
        overall_health_score=15.0 if sev == "CRITICAL" else (45.0 if sev == "SEVERE" else 65.0),
        overall_health_status=status_label,
        iso_vibration_zone=iso_zone,
        cable_check=cable,
        defect_localization=defect,
        rul_prediction=rul,
    )

    frame = TelemetryFrame(
        machineId=machine_id,
        imuAcceleration=8.45 if sev == "CRITICAL" else (5.2 if sev == "SEVERE" else 3.8),
        tempMotor=88.5,
        tempCompressor=76.0,
        emIr=54.2,
        emIy=53.8,
        emIb=54.0,
        emVr=412.0,
        emVy=411.0,
        emVb=413.0,
        emMachineLoad=92.0,
        emVoltageImbalance=0.8,
        emPower=38.5,
    )

    result = dispatch_alert_notifications(
        machine_id=machine_id,
        prediction_response=pred,
        telemetry_frame=frame,
        force=True,
        custom_note=req.custom_note,
    )

    return {
        "ok": True,
        "dispatched": result.get("dispatched"),
        "severity": result.get("severity"),
        "whatsapp": result.get("whatsapp"),
        "email": result.get("email"),
        "record": result.get("record"),
    }


@router.post(
    "/alerts/clear",
    summary="Clear Alert Cooldown / Anti-Spam Lock for Asset",
)
async def clear_alert_cooldown(req: ClearCooldownRequest):
    """Manually clears debounce cooldown for an asset so fresh alerts can fire immediately."""
    note_alert_cleared(req.machine_id)
    return {
        "ok": True,
        "machine_id": req.machine_id,
        "message": f"Alert cooldown cleared for {req.machine_id}",
    }
