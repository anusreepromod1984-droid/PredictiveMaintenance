"""
Multi-Channel Industrial Alert Dispatcher (WhatsApp Business API & Email Gateway)
Dispatches urgent notifications when critical or severe machine defects/abnormalities occur.
Integrates Meta WhatsApp Cloud API and Splus SendMailAsync Gateway with anti-spam cooldown.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src.config import settings
from src.schemas.predictions import PredictRULResponse
from src.schemas.telemetry import TelemetryFrame
from src.utils.logger import get_logger

logger = get_logger("Services.AlertDispatcher")

# Severity Hierarchy
SEVERITY_LEVELS: Dict[str, int] = {
    "CRITICAL": 3,
    "SEVERE": 2,
    "WARNING": 1,
    "NORMAL": 0,
    "HEALTHY": 0,
}

_SEVERITY_EMOJIS = {
    "CRITICAL": "🔴 CRITICAL",
    "SEVERE": "🟠 SEVERE",
    "WARNING": "🟡 WARNING",
    "NORMAL": "🟢 NORMAL",
}

_SEVERITY_COLORS = {
    "CRITICAL": "#ef4444",
    "SEVERE": "#f97316",
    "WARNING": "#eab308",
    "NORMAL": "#22c55e",
}

# Machine asset display labels
_ASSET_NAMES: Dict[str, str] = {
    "compressor_unit_01": "Compressor Unit 01 — Elson EL30 (720 RPM, Reciprocating)",
    "motor_drive_02": "Induction Motor Drive 02 (30 kW, 1480 RPM)",
    "chiller_pump_03": "Centrifugal Chiller Pump 03 (45 kW)",
}

# In-memory deduplication & history
_lock = threading.Lock()
_alert_cooldowns: Dict[str, Dict[str, Any]] = {}
_alert_history: List[Dict[str, Any]] = []

_redis_client = None
_redis_checked = False


def _get_redis():
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True
    if not settings.REDIS_ENABLED:
        return None
    try:
        import redis
        client = redis.Redis.from_url(
            settings.get_redis_url(),
            decode_responses=True,
            socket_timeout=1.5,
        )
        client.ping()
        _redis_client = client
    except Exception as exc:
        logger.warning("[AlertDispatcher] Redis unavailable (%s). Falling back to memory.", exc)
        _redis_client = None
    return _redis_client


def _cooldown_redis_key(machine_id: str, fault_key: str) -> str:
    return f"{settings.REDIS_KEY_PREFIX}:alert_cd:{machine_id}:{fault_key}"


def _get_friendly_asset_name(machine_id: str) -> str:
    if machine_id in _ASSET_NAMES:
        return _ASSET_NAMES[machine_id]
    return machine_id.replace("_", " ").title()


def _format_ist_time(dt: Optional[datetime] = None) -> str:
    if not dt:
        dt = datetime.now(timezone.utc)
    # Convert UTC to IST (+5:30)
    ist = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
    return ist.strftime("%Y-%m-%d %I:%M:%S %p IST")


def evaluate_alert_severity(
    prediction: PredictRULResponse,
    frame: Optional[TelemetryFrame] = None,
) -> Tuple[str, int, str]:
    """
    Evaluates whether an alert condition exists and assigns a severity level:
    - CRITICAL (Level 3): ISO Zone D, RUL <= 14 days with active defect, health_status == CRITICAL.
    - SEVERE (Level 2): Hardware/cable fault (Alpha), ISO Zone C, RUL <= 30 days, health_status == SENSOR_FAULT.
    - WARNING (Level 1): Health status WATCH / DEGRADED, active defect with RUL > 30 days.
    - NORMAL (Level 0): Safe, healthy baseline.

    Returns:
        (severity_label, severity_score, reason)
    """
    status = (prediction.overall_health_status or "HEALTHY").upper()
    iso_zone = (prediction.iso_vibration_zone or "").upper()
    cable_status = (getattr(prediction.cable_check, "status", "VALID") or "VALID").upper()
    defect = prediction.defect_localization
    defect_code = (defect.defect_code if defect else "NORMAL").upper()
    rul_days = prediction.rul_prediction.rul_days if prediction.rul_prediction else 999.0

    # 1. Critical conditions
    if status == "CRITICAL" or iso_zone == "D":
        reason = f"Critical condition detected: Overall Status={status}, ISO 20816 Zone={iso_zone}"
        if defect and defect_code not in {"NORMAL", "NONE", ""}:
            reason += f", Defect={defect.defect_name} ({defect_code}), RUL={rul_days:.1f}d"
        return "CRITICAL", 3, reason

    if defect and defect_code not in {"NORMAL", "NONE", ""} and rul_days <= 14.0:
        return "CRITICAL", 3, f"Imminent failure risk: {defect.defect_name} ({defect_code}) with RUL of {rul_days:.1f} days"

    # 2. Severe conditions
    if cable_status not in {"VALID", "NORMAL", "GOOD", ""}:
        reason = f"Hardware sensor fault / signal disruption: {cable_status}"
        if prediction.cable_check and prediction.cable_check.fault_reason:
            reason += f" - {prediction.cable_check.fault_reason}"
        return "SEVERE", 2, reason

    if status == "SENSOR_FAULT":
        return "SEVERE", 2, "Transducer / Sensor hardware failure halted diagnostic DAG"

    if status == "DEGRADED" and rul_days <= 30.0:
        defect_str = f"{defect.defect_name} ({defect_code})" if defect else "Mechanical Degradation"
        return "SEVERE", 2, f"Severe asset degradation: {defect_str}, RUL={rul_days:.1f} days"

    if iso_zone == "C":
        return "SEVERE", 2, f"Elevated vibration in ISO 20816 Zone C (Unsatisfactory threshold breached)"

    # 3. Warning conditions
    if defect and defect_code not in {"NORMAL", "NONE", ""}:
        return "WARNING", 1, f"Developing defect identified: {defect.defect_name} ({defect_code}), RUL={rul_days:.1f} days"

    if status in {"DEGRADED", "WATCH"} or (prediction.overall_health_score and prediction.overall_health_score < 70.0):
        return "WARNING", 1, f"Asset health degraded (Score: {prediction.overall_health_score:.1f}%, Status: {status})"

    return "NORMAL", 0, "Asset operating within nominal limits"


def _clean_phone_number(raw_phone: str) -> str:
    """Strips spaces, hyphens, and leading '+' for Meta WhatsApp API."""
    cleaned = re.sub(r"[^\d]", "", raw_phone.strip())
    return cleaned


def send_whatsapp_alert(
    text: str,
    recipient: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends WhatsApp alert message via Meta WhatsApp Business Cloud API.
    Uses WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID from .env.
    """
    token = (settings.WHATSAPP_TOKEN or "").strip()
    phone_number_id = (settings.WHATSAPP_PHONE_NUMBER_ID or "").strip()

    if not token or not phone_number_id:
        msg = "WhatsApp API credentials not configured (WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID)"
        logger.warning("[AlertDispatcher] %s", msg)
        return {"success": False, "error": msg}

    target_numbers = [
        _clean_phone_number(p)
        for p in (recipient or settings.WHATSAPP_ALERT_TO or "").split(",")
        if _clean_phone_number(p)
    ]

    if not target_numbers:
        msg = "No valid WhatsApp destination phone numbers configured"
        logger.warning("[AlertDispatcher] %s", msg)
        return {"success": False, "error": msg}

    api_version = getattr(settings, "WHATSAPP_API_VERSION", "v18.0")
    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    results = []
    has_success = False

    with httpx.Client(timeout=getattr(settings, "WHATSAPP_TIMEOUT_S", 15.0)) as client:
        for phone in target_numbers:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": phone,
                "type": "text",
                "text": {
                    "preview_url": False,
                    "body": text,
                },
            }
            try:
                resp = client.post(url, json=payload, headers=headers)
                if resp.status_code in {200, 201}:
                    has_success = True
                    data = resp.json()
                    msg_id = (data.get("messages") or [{}])[0].get("id", "ok")
                    logger.info("[AlertDispatcher] WhatsApp sent to %s (id=%s)", phone, msg_id)
                    results.append({"phone": phone, "status": "sent", "id": msg_id})
                else:
                    err_msg = resp.text[:300]
                    logger.error("[AlertDispatcher] WhatsApp send to %s failed (%s): %s", phone, resp.status_code, err_msg)
                    results.append({"phone": phone, "status": "failed", "code": resp.status_code, "error": err_msg})
            except Exception as exc:
                logger.error("[AlertDispatcher] WhatsApp request error for %s: %s", phone, exc)
                results.append({"phone": phone, "status": "error", "error": str(exc)})

    return {
        "success": has_success,
        "results": results,
    }


def send_email_alert(
    subject: str,
    message: str,
    recipient: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends companion Email alert via Splus SendMailAsync Gateway.
    Uses OEM_MAIL_API_URL and ALERT_MAIL_TO / OEM_MAIL_TO from .env.
    """
    url = (settings.OEM_MAIL_API_URL or "").strip()
    to_addr = (recipient or getattr(settings, "ALERT_MAIL_TO", "") or settings.OEM_MAIL_TO or "").strip()

    if not url:
        msg = "OEM_MAIL_API_URL is not set in .env"
        logger.warning("[AlertDispatcher] %s", msg)
        return {"success": False, "error": msg}

    if not to_addr:
        msg = "Recipient email (ALERT_MAIL_TO / OEM_MAIL_TO) is not set in .env"
        logger.warning("[AlertDispatcher] %s", msg)
        return {"success": False, "error": msg}

    payload = {
        "From": settings.OEM_MAIL_FROM or "",
        "Pwd": settings.OEM_MAIL_PWD or "",
        "To": to_addr,
        "CC": (settings.OEM_MAIL_CC or "").strip(),
        "Bcc": (settings.OEM_MAIL_BCC or "").strip(),
        "Subject": subject,
        "Message": message,
        "SpClientId": (settings.OEM_MAIL_SP_CLIENT_ID or "").strip(),
    }

    try:
        with httpx.Client(timeout=settings.OEM_MAIL_TIMEOUT_S) as client:
            resp = client.post(url, json=payload, headers={"Content-Type": "application/json"})
        if resp.status_code < 400:
            logger.info("[AlertDispatcher] Email sent to %s, subject=%r", to_addr, subject)
            return {"success": True, "status_code": resp.status_code, "to": to_addr}
        else:
            err = f"SendMailAsync gateway returned {resp.status_code}: {resp.text[:300]}"
            logger.error("[AlertDispatcher] %s", err)
            return {"success": False, "status_code": resp.status_code, "error": err}
    except Exception as exc:
        logger.error("[AlertDispatcher] Email send exception: %s", exc)
        return {"success": False, "error": str(exc)}


def build_whatsapp_alert_text(
    machine_id: str,
    severity: str,
    prediction: PredictRULResponse,
    frame: Optional[TelemetryFrame] = None,
    custom_note: Optional[str] = None,
) -> str:
    """Builds clean, structured industrial alert for WhatsApp."""
    machine_name = _get_friendly_asset_name(machine_id)
    badge = _SEVERITY_EMOJIS.get(severity, f"⚠️ {severity}")
    defect = prediction.defect_localization
    rul = prediction.rul_prediction
    cable = prediction.cable_check
    timestamp_str = _format_ist_time(prediction.timestamp)

    defect_title = defect.defect_name if defect else (
        cable.fault_reason or cable.status if cable and cable.status != "VALID" else "Anomaly Detected"
    )
    defect_code = defect.defect_code if defect else (cable.status if cable else "ANOMALY")
    confidence = f"{defect.confidence_percentage:.0f}%" if defect and defect.confidence_percentage else "N/A"
    failing_part = defect.failing_component if defect else "Sensor / Electrical Circuit"

    rul_str = f"{rul.rul_days:.1f} days ({rul.rul_operating_hours:.0f} hrs)" if rul else "Immediate inspection required"
    repair_by = rul.recommended_repair_by_date if rul and rul.recommended_repair_by_date else "Today"

def _extract_telemetry_metrics(frame: Optional[Any], prediction: PredictRULResponse) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    if not frame:
        if prediction.electrical_health:
            eh = prediction.electrical_health
            if getattr(eh, "machine_load_pct", None) is not None:
                metrics["load_pct"] = float(eh.machine_load_pct)
            if getattr(eh, "voltage_unbalance_pct", None) is not None:
                metrics["vuf_pct"] = float(eh.voltage_unbalance_pct)
        return metrics

    # Vibration: imu_acceleration or vibration_rms
    vib = getattr(frame, "imu_acceleration", None)
    if vib is None:
        vib = getattr(frame, "vibration_rms", None)
    if vib is not None:
        metrics["vibration_rms"] = float(vib)

    # Motor Temp: temp_motor or temperature_motor
    tm = getattr(frame, "temp_motor", None)
    if tm is None:
        tm = getattr(frame, "temperature_motor", None)
    if tm is not None:
        metrics["temp_motor"] = float(tm)

    # Compressor Temp: temp_compressor or temperature_compressor
    tc = getattr(frame, "temp_compressor", None)
    if tc is None:
        tc = getattr(frame, "temperature_compressor", None)
    if tc is not None and float(tc) > 0.0:
        metrics["temp_compressor"] = float(tc)

    # Electrical load: em_machine_load or machine_load_pct
    load = getattr(frame, "em_machine_load", None)
    if load is None:
        load = getattr(frame, "machine_load_pct", None)
    if load is not None:
        metrics["load_pct"] = float(load)

    # Current: average of em_ir, em_iy, em_ib or em_current_rms
    ir = getattr(frame, "em_ir", None)
    iy = getattr(frame, "em_iy", None)
    ib = getattr(frame, "em_ib", None)
    if ir is not None and iy is not None and ib is not None:
        metrics["current_rms"] = (float(ir) + float(iy) + float(ib)) / 3.0
    elif getattr(frame, "em_current_rms", None) is not None:
        metrics["current_rms"] = float(frame.em_current_rms)

    # Voltage: em_vr, em_vy, em_vb
    vr = getattr(frame, "em_vr", None)
    vy = getattr(frame, "em_vy", None)
    vb = getattr(frame, "em_vb", None)
    if vr is not None and vy is not None and vb is not None:
        metrics["voltage_rms"] = (float(vr) + float(vy) + float(vb)) / 3.0

    return metrics


def build_whatsapp_alert_text(
    machine_id: str,
    severity: str,
    prediction: PredictRULResponse,
    frame: Optional[TelemetryFrame] = None,
    custom_note: Optional[str] = None,
) -> str:
    """Builds clean, structured industrial alert for WhatsApp."""
    machine_name = _get_friendly_asset_name(machine_id)
    badge = _SEVERITY_EMOJIS.get(severity, f"⚠️ {severity}")
    defect = prediction.defect_localization
    rul = prediction.rul_prediction
    cable = prediction.cable_check
    timestamp_str = _format_ist_time(prediction.timestamp)

    defect_title = defect.defect_name if defect else (
        cable.fault_reason or cable.status if cable and cable.status != "VALID" else "Anomaly Detected"
    )
    defect_code = defect.defect_code if defect else (cable.status if cable else "ANOMALY")
    confidence = f"{defect.confidence_percentage:.0f}%" if defect and defect.confidence_percentage else "N/A"
    failing_part = defect.failing_component if defect else "Sensor / Electrical Circuit"

    rul_str = f"{rul.rul_days:.1f} days ({rul.rul_operating_hours:.0f} hrs)" if rul else "Immediate inspection required"
    repair_by = rul.recommended_repair_by_date if rul and rul.recommended_repair_by_date else "Today"

    # Key telemetry highlights
    metrics = _extract_telemetry_metrics(frame, prediction)
    lines_telemetry = []
    if "vibration_rms" in metrics:
        iso_tag = f" [ISO Zone {prediction.iso_vibration_zone}]" if prediction.iso_vibration_zone else ""
        lines_telemetry.append(f"• Vibration: {metrics['vibration_rms']:.2f} mm/s RMS{iso_tag}")
    if "temp_motor" in metrics:
        lines_telemetry.append(f"• Motor Temp: {metrics['temp_motor']:.1f} °C")
    if "temp_compressor" in metrics:
        lines_telemetry.append(f"• Compressor Temp: {metrics['temp_compressor']:.1f} °C")
    if "current_rms" in metrics:
        lines_telemetry.append(f"• Current: {metrics['current_rms']:.1f} A")
    if "load_pct" in metrics:
        lines_telemetry.append(f"• Motor Load: {metrics['load_pct']:.1f} %")
    if "vuf_pct" in metrics:
        lines_telemetry.append(f"• Voltage Unbalance: {metrics['vuf_pct']:.2f} %")

    telemetry_block = "\n".join(lines_telemetry) if lines_telemetry else "• Real-time telemetry streaming active"

    # Action summary
    action = "Immediate technician dispatch recommended."
    if defect and defect.expert_repair_guidance:
        g = defect.expert_repair_guidance
        if g.immediate_field_triage:
            action = g.immediate_field_triage
    elif cable and cable.status != "VALID":
        action = f"Check sensor loop wiring: {cable.fault_reason or 'Transducer out of range'}."

    text = f"""*🚨 APMS INDUSTRIAL ALERT: {badge}*
━━━━━━━━━━━━━━━━━━━━
🏭 *Asset:* {machine_name}
🆔 *ID:* `{machine_id}`
🔍 *Diagnosis:* {defect_title} (`{defect_code}`)
🧩 *Failing Part:* {failing_part}
📊 *Confidence:* {confidence}
⏱️ *Remaining Useful Life:* {rul_str}
📅 *Repair Deadline:* {repair_by}

📈 *Telemetry Metrics:*
{telemetry_block}

🛠️ *Immediate Action / Triage:*
{action}

🕒 *Timestamp:* {timestamp_str}
━━━━━━━━━━━━━━━━━━━━
*Greenbotz Predictive Maintenance AI*"""

    if custom_note:
        text += f"\n\n📝 *Note:* {custom_note}"

    return text


def _clean_subject_title(raw_title: str) -> str:
    """Creates a clean, punchy title suitable for email subject lines without multi-line clutter."""
    if not raw_title:
        return "Operational Anomaly"
    low = raw_title.lower()
    if "diverged by" in low or ("temperature" in low and "drift" in low):
        return "Thermal Sensor Divergence (Delta-T Limit Exceeded)"
    if "cable" in low or "disconnect" in low:
        return "Sensor Hardware Cable Fault"
    if "field_not_present" in low:
        return "Telemetry Signal Missing (FIELD_NOT_PRESENT)"
    if "loop" in low and "ma" in low:
        return "NAMUR NE43 Current Loop Fault"
    if "bpfi" in low:
        return "Bearing Inner Race Defect (BPFI)"
    if "bpfo" in low:
        return "Bearing Outer Race Defect (BPFO)"
    if "bsf" in low:
        return "Bearing Ball Element Defect (BSF)"
    if "ftf" in low:
        return "Bearing Cage Defect (FTF)"
    if "looseness" in low:
        return "Mechanical Looseness (MF001)"
    if "misalignment" in low:
        return "Shaft Misalignment (MF002)"
    if "imbalance" in low:
        return "Dynamic Rotor Imbalance (MF003)"
    first_sentence = raw_title.split(".")[0].strip()
    if len(first_sentence) > 55:
        return first_sentence[:52] + "..."
    return first_sentence


def build_email_alert_message(
    machine_id: str,
    severity: str,
    prediction: PredictRULResponse,
    frame: Optional[TelemetryFrame] = None,
    custom_note: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Builds a beautifully polished, executive-ready industrial alert email.
    Formatted with clear visual hierarchy, aligned data columns, and actionable triage.
    """
    machine_name = _get_friendly_asset_name(machine_id)
    badge = _SEVERITY_EMOJIS.get(severity, severity)
    defect = prediction.defect_localization
    rul = prediction.rul_prediction
    cable = prediction.cable_check
    timestamp_str = _format_ist_time(prediction.timestamp)

    defect_title = defect.defect_name if defect else (
        cable.fault_reason or cable.status if cable and cable.status != "VALID" else "Condition Anomaly"
    )
    defect_code = defect.defect_code if defect else (cable.status if cable else "ANOMALY")
    confidence = f"{defect.confidence_percentage:.1f}%" if defect and defect.confidence_percentage else "High"
    failing_part = defect.failing_component if defect else "Sensor / Electrical Transducer Circuit"

    rul_str = f"{rul.rul_days:.1f} days ({rul.rul_operating_hours:.0f} operating hours)" if rul else "Immediate Attention"
    repair_by = rul.recommended_repair_by_date if rul and rul.recommended_repair_by_date else "Today"

    # Action & triage
    action_text = "Schedule physical inspection and evaluate component condition immediately."
    triage_steps = []
    root_cause = "Real-time AI diagnostic rules identified abnormal operating patterns."
    if defect and defect.expert_repair_guidance:
        g = defect.expert_repair_guidance
        if g.immediate_field_triage:
            action_text = g.immediate_field_triage
        if g.root_cause_mechanism:
            root_cause = g.root_cause_mechanism
        if g.planned_overhaul_playbook and g.planned_overhaul_playbook.step_by_step_instructions:
            triage_steps = g.planned_overhaul_playbook.step_by_step_instructions[:5]
    elif cable and cable.status != "VALID":
        action_text = f"Verify transducer wiring and terminal connections: {cable.fault_reason or cable.status}."
        root_cause = f"Analog loop / communication fault detected on sensor channel: {cable.fault_reason or cable.status}."

    # Subject line
    subject_title = _clean_subject_title(defect_title)
    short_name = machine_name.split("—")[0].strip() if "—" in machine_name else machine_name
    subject = f"[{badge}] APMS Alert: {short_name} — {subject_title}"

    # Telemetry lines
    metrics = _extract_telemetry_metrics(frame, prediction)
    telemetry_lines = []
    if "vibration_rms" in metrics:
        iso_tag = f" [ISO Zone {prediction.iso_vibration_zone} Breached]" if prediction.iso_vibration_zone else ""
        telemetry_lines.append(f"  • Vibration Velocity RMS    : {metrics['vibration_rms']:.2f} mm/s RMS{iso_tag}")
    if "temp_motor" in metrics:
        telemetry_lines.append(f"  • Motor Stator Temperature  : {metrics['temp_motor']:.1f} °C (Limit: 85°C Warning | 95°C Trip)")
    if "temp_compressor" in metrics:
        telemetry_lines.append(f"  • Compressor Air-End Temp   : {metrics['temp_compressor']:.1f} °C (Limit: 80°C Warning | 90°C Trip)")
    if "current_rms" in metrics:
        telemetry_lines.append(f"  • Line Current (3-Phase RMS): {metrics['current_rms']:.1f} A (Full Load Amps: 68.0 A)")
    if "load_pct" in metrics:
        telemetry_lines.append(f"  • Operational Machine Load  : {metrics['load_pct']:.1f} % (Nominal Range: 40–85 %)")
    if "voltage_rms" in metrics:
        telemetry_lines.append(f"  • Grid Supply Voltage       : {metrics['voltage_rms']:.1f} V (Nominal Band: 380–435 V)")
    if "vuf_pct" in metrics:
        telemetry_lines.append(f"  • Voltage Unbalance (VUF)   : {metrics['vuf_pct']:.2f} % (IEC Limit: 1.5 %)")

    telemetry_section = "\n".join(telemetry_lines) if telemetry_lines else "  • Continuous streaming sensor telemetry active"

    # Step-by-step procedure lines
    procedure_lines = []
    if triage_steps:
        for idx, step in enumerate(triage_steps, start=1):
            procedure_lines.append(f"  {idx}. {step}")
    else:
        procedure_lines.append("  1. Notify on-duty maintenance engineer and verify physical machine state.")
        procedure_lines.append("  2. Review vibration spectral envelope and temperature trends in the APMS dashboard.")
        procedure_lines.append("  3. Schedule necessary downtime or corrective overhaul before the repair deadline.")

    procedure_section = "\n".join(procedure_lines)
    note_section = f"\nOPERATIONS NOTE:\n{custom_note}\n" if custom_note else ""

    body = f"""================================================================================
🚨 APMS INDUSTRIAL TELEMETRY ALERT — {badge}
================================================================================

Greenbotz Predictive Maintenance AI Engine
Plant Reliability Operations & Asset Protection
Timestamp: {timestamp_str}

--------------------------------------------------------------------------------
1. ASSET IDENTIFICATION
--------------------------------------------------------------------------------
  Asset Name    : {machine_name}
  Asset ID      : {machine_id}
  Health Status : {badge} (Health Score: {prediction.overall_health_score:.1f}%)
  Vibration ISO : Zone {prediction.iso_vibration_zone or 'N/A'} (ISO 20816-3 Standard)

--------------------------------------------------------------------------------
2. AI DIAGNOSTIC SUMMARY & IMPACT
--------------------------------------------------------------------------------
  Primary Fault : {defect_title}
  Fault Code    : {defect_code}
  Failing Part  : {failing_part}
  Confidence    : {confidence}
  Remaining Life: {rul_str}
  Repair Due By : {repair_by}

  Root Cause / Mechanism:
  {root_cause}

--------------------------------------------------------------------------------
3. LIVE SENSOR TELEMETRY SNAPSHOT
--------------------------------------------------------------------------------
{telemetry_section}

--------------------------------------------------------------------------------
4. PRESCRIPTIVE MAINTENANCE & IMMEDIATE TRIAGE
--------------------------------------------------------------------------------
  Immediate Field Triage:
  {action_text}

  Recommended Procedure:
{procedure_section}
{note_section}
--------------------------------------------------------------------------------
This is an automated industrial telemetry alert dispatched by the Greenbotz
Agentic Predictive & Preventive Maintenance System (APMS).
Plant Reliability & Asset Protection Operations
================================================================================
"""
    return subject, body


# Backward compatibility alias
build_email_alert_html = build_email_alert_message



def dispatch_alert_notifications(
    machine_id: str,
    prediction_response: PredictRULResponse,
    telemetry_frame: Optional[TelemetryFrame] = None,
    *,
    force: bool = False,
    custom_note: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main entry point for evaluating and dispatching multi-channel alerts (WhatsApp + Email).
    Respects cooldown debounce unless force=True or severity escalates.
    """
    if not getattr(settings, "ALERT_NOTIFICATION_ENABLED", True):
        return {"dispatched": False, "reason": "Alert notifications disabled in configuration"}

    severity_label, severity_score, reason = evaluate_alert_severity(prediction_response, telemetry_frame)

    min_severity = getattr(settings, "ALERT_MIN_SEVERITY", "WARNING").upper()
    min_score = SEVERITY_LEVELS.get(min_severity, 1)

    if severity_score < min_score and not force:
        return {
            "dispatched": False,
            "severity": severity_label,
            "reason": f"Severity {severity_label} (score {severity_score}) is below minimum {min_severity} (score {min_score})",
        }

    # Determine unique defect / condition key for debouncing
    defect = prediction_response.defect_localization
    cable = prediction_response.cable_check
    defect_key = defect.defect_code if defect and defect.defect_code != "NORMAL" else (
        cable.status if cable and cable.status != "VALID" else prediction_response.overall_health_status
    )
    cache_key = f"{machine_id}:{defect_key}"

    now_ts = time.time()
    cooldown_s = getattr(settings, "ALERT_NOTIFICATION_COOLDOWN_SECONDS", 1800)

    # Check cooldown & escalation
    with _lock:
        prior = _alert_cooldowns.get(cache_key)
        if prior and not force:
            elapsed = now_ts - prior.get("sent_at", 0)
            prior_score = prior.get("severity_score", 0)
            if elapsed < cooldown_s:
                # Severity escalation allows immediate re-send
                if severity_score > prior_score:
                    logger.info(
                        "[AlertDispatcher] Severity escalated for %s (%s -> %s). Bypassing cooldown.",
                        cache_key,
                        prior.get("severity"),
                        severity_label,
                    )
                else:
                    return {
                        "dispatched": False,
                        "severity": severity_label,
                        "reason": f"Alert on cooldown for {cache_key} ({int(cooldown_s - elapsed)}s remaining). Prior severity: {prior.get('severity')}",
                    }

        # Check Redis if available
        r = _get_redis()
        if r and not force:
            try:
                r_key = _cooldown_redis_key(machine_id, defect_key)
                r_val = r.get(r_key)
                if r_val:
                    r_score = int(r_val)
                    if severity_score <= r_score:
                        return {
                            "dispatched": False,
                            "severity": severity_label,
                            "reason": f"Alert on Redis cooldown for {cache_key}",
                        }
            except Exception as rex:
                logger.warning("[AlertDispatcher] Redis cooldown check failed: %s", rex)

    # Build messages
    wa_text = build_whatsapp_alert_text(
        machine_id=machine_id,
        severity=severity_label,
        prediction=prediction_response,
        frame=telemetry_frame,
        custom_note=custom_note,
    )

    mail_subject, mail_html = build_email_alert_html(
        machine_id=machine_id,
        severity=severity_label,
        prediction=prediction_response,
        frame=telemetry_frame,
        custom_note=custom_note,
    )

    # Dispatch WhatsApp
    wa_result = send_whatsapp_alert(wa_text)

    # Dispatch Email
    email_result = send_email_alert(mail_subject, mail_html)

    # Update cooldown & history
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "machine_id": machine_id,
        "defect_key": defect_key,
        "severity": severity_label,
        "severity_score": severity_score,
        "sent_at": now_ts,
        "whatsapp": wa_result,
        "email": email_result,
        "reason": reason,
    }

    with _lock:
        _alert_cooldowns[cache_key] = record
        _alert_history.append(record)
        if len(_alert_history) > 100:
            _alert_history.pop(0)

    r = _get_redis()
    if r:
        try:
            r_key = _cooldown_redis_key(machine_id, defect_key)
            r.set(r_key, str(severity_score), ex=cooldown_s)
        except Exception as rex:
            logger.warning("[AlertDispatcher] Redis cooldown set failed: %s", rex)

    logger.info(
        "[AlertDispatcher] Dispatched alerts for %s (%s). WhatsApp: %s, Email: %s",
        machine_id,
        severity_label,
        wa_result.get("success"),
        email_result.get("success"),
    )

    return {
        "dispatched": True,
        "severity": severity_label,
        "severity_score": severity_score,
        "whatsapp": wa_result,
        "email": email_result,
        "record": record,
    }


def note_alert_cleared(machine_id: str) -> None:
    """Clears cooldown state when machine returns to a confirmed healthy state."""
    with _lock:
        keys_to_clear = [k for k in _alert_cooldowns if k.startswith(f"{machine_id}:")]
        for k in keys_to_clear:
            _alert_cooldowns.pop(k, None)

    r = _get_redis()
    if r:
        try:
            pattern = _cooldown_redis_key(machine_id, "*")
            keys = r.keys(pattern)
            if keys:
                r.delete(*keys)
        except Exception as exc:
            logger.warning("[AlertDispatcher] Redis clear error: %s", exc)


def get_alert_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Returns recent dispatched alerts history."""
    with _lock:
        return list(reversed(_alert_history[-limit:]))
