"""
Socket.IO Server — real-time push layer over the FastAPI REST API.

Payloads match the Next.js RealtimeProvider contract (Gbotz frontend):
  bootstrap       — { upstream, schema, machines, records, activeAlerts }
  telemetry       — { machineId, telemetry } with nested temperature/energyMeter/...
  alert           — { machineId, breaches, activity }
  upstream:status — { state, lastMessageAt, since }
  machines:update — MachineMeta[]

Mounted at /socket.io via socketio.ASGIApp wrapping the FastAPI app.
"""

import hashlib
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import socketio
from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("SocketIO")

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)

_mqtt_service = None
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_upstream_since_ms: Optional[int] = None

# Same default placements the Next.js /api/machines route uses — PlantMap hides
# machines whose mapX/mapY are null ("No machines have been placed").
_FLOOR_COORDS: Tuple[Tuple[float, float], ...] = (
    (25.0, 35.0),
    (55.0, 45.0),
    (75.0, 65.0),
)

SENSOR_SCHEMA: Dict[str, Any] = {
    "parameters": [
        {"name": "imuAcceleration", "unit": "mm/s", "data": "float", "parameters": None},
        {"name": "rpm", "unit": "rpm", "data": "float", "parameters": None},
        {"name": "temperature.motor", "unit": "°C", "data": "float", "parameters": None},
        {"name": "temperature.compressor", "unit": "°C", "data": "float", "parameters": None},
        {"name": "energyMeter.Ir", "unit": "A", "data": "float", "parameters": None},
        {"name": "energyMeter.power", "unit": "kW", "data": "float", "parameters": None},
        {"name": "microphone.soundLevel", "unit": "dB", "data": "float", "parameters": None},
        {"name": "humidity", "unit": "%", "data": "float", "parameters": None},
        {"name": "pressure", "unit": "bar", "data": "float", "parameters": None},
    ]
}


def set_mqtt_service_for_sio(service) -> None:
    """Called from main.py startup to wire the MQTT service into Socket.IO."""
    global _mqtt_service, _main_loop, _upstream_since_ms
    _mqtt_service = service
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        _main_loop = None
    _upstream_since_ms = _now_ms()


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _num(raw: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        val = raw.get(key)
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return default


def _has_source_key(available: list, *aliases: str) -> bool:
    sent = {str(k).replace("_", "").lower() for k in (available or [])}
    return any(alias.replace("_", "").lower() in sent for alias in aliases)


def to_frontend_telemetry(raw: Dict[str, Any], machine_id: str, ts_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Map a Python TelemetryFrame dump onto the nested shape RealtimeProvider.normalizeTelemetry expects."""
    if not raw:
        return None
    mid = raw.get("machineId") or raw.get("machine_id") or machine_id
    if not mid:
        return None
    ts = ts_ms if ts_ms is not None else raw.get("timestamp")
    if isinstance(ts, datetime):
        ts = int(ts.timestamp() * 1000)
    if not isinstance(ts, (int, float)):
        ts = _now_ms()
    harmonics = raw.get("vibrationHarmonics") or raw.get("vibration_harmonics") or []
    if not isinstance(harmonics, list):
        harmonics = []
    mic_harmonics = raw.get("micHarmonics") or raw.get("mic_harmonics") or []
    if not isinstance(mic_harmonics, list):
        mic_harmonics = []
    available = raw.get("sourceKeys") or raw.get("source_keys") or []
    missing = raw.get("missingBlocks") or raw.get("missing_blocks") or []
    if not isinstance(available, list):
        available = []
    if not isinstance(missing, list):
        missing = []
    compressor = _num(raw, "tempCompressor", "temp_compressor")
    if not _has_source_key(available, "tempCompressor") and _has_source_key(available, "tempAmbient"):
        compressor = _num(raw, "tempAmbient", "temp_ambient")
    reported_power = _num(raw, "emPower", "em_power")
    estimated_power = _num(raw, "emPowerEstimated", "em_power_estimated")
    power_estimated = reported_power < 0.05 and estimated_power > 0.05
    # Energy tiles must match pdm/electricity. kWh-slope kW stays in estimatedPower.
    display_power = reported_power
    reported_load = _num(raw, "emMachineLoad", "em_machine_load")
    rated = max(float(settings.MOTOR_RATED_KW), 1.0)
    nameplate_load = min(100.0, display_power / rated * 100.0) if display_power > 0.05 else 0.0
    i_max = max(_num(raw, "emIr", "em_ir"), _num(raw, "emIy", "em_iy"), _num(raw, "emIb", "em_ib"))
    if i_max < 0.5 and display_power < 0.2:
        display_load = 0.0
    elif nameplate_load > 0.0 and abs(reported_load - nameplate_load) > 20.0:
        display_load = round(nameplate_load, 1)
    else:
        display_load = reported_load
    return {
        "machineId": str(mid),
        "timestamp": int(ts),
        "availableFields": available,
        "missingFields": missing,
        "imuAcceleration": _num(raw, "imuAcceleration", "imu_acceleration"),
        "xAxisVibration": _num(raw, "xAxisVibration", "x_axis_vibration"),
        "yAxisVibration": _num(raw, "yAxisVibration", "y_axis_vibration"),
        "zAxisVibration": _num(raw, "zAxisVibration", "z_axis_vibration"),
        "rpm": _num(raw, "rpm"),
        "vibration": {"harmonics": harmonics},
        "motorFaults": raw.get("motorFaults") or raw.get("motor_faults") or [],
        "temperature": {
            "motor": _num(raw, "tempMotor", "temp_motor"),
            "compressor": compressor,
        },
        "energyMeter": {
            "Ir": _num(raw, "emIr", "em_ir"),
            "Iy": _num(raw, "emIy", "em_iy"),
            "Ib": _num(raw, "emIb", "em_ib"),
            "machineLoad": display_load,
            "Vr": _num(raw, "emVr", "em_vr"),
            "Vy": _num(raw, "emVy", "em_vy"),
            "Vb": _num(raw, "emVb", "em_vb"),
            "voltageImbalance": _num(raw, "emVoltageImbalance", "em_voltage_imbalance"),
            "power": display_power,
            "powerEstimated": power_estimated,
            "estimatedPower": estimated_power,
            "energy": _num(raw, "emEnergy", "em_energy"),
            "averagePowerFactor": _num(raw, "emPowerFactor", "emAveragePowerFactor", "em_power_factor", default=0.92),
            "thdVr": _num(raw, "emThdVr", "em_thd_v"),
            "thdVy": _num(raw, "emThdVy", "em_thd_vy"),
            "thdVb": _num(raw, "emThdVb", "em_thd_vb"),
            "frequency": _num(raw, "emFrequency", "em_frequency"),
            "frequencyDeviation": _num(raw, "emFrequencyDeviation", "em_frequency_deviation"),
        },
        "pressure": _num(raw, "pressure"),
        "microphone": {
            "soundLevel": _num(raw, "soundLevel", "sound_level"),
            "harmonics": mic_harmonics,
        },
        "humidity": _num(raw, "humidity"),
        "sensorStatus": {
            "ok": True,
            "message": f"Unavailable fields: {', '.join(str(v) for v in missing)}" if missing else "All expected sensor fields received",
        },
        "magnetometer": {
            "roll": _num(raw, "magRoll", "mag_roll"),
            "pitch": _num(raw, "magPitch", "mag_pitch"),
            "yaw": _num(raw, "magYaw", "mag_yaw"),
        },
        "runtime": {
            "machineRunHours": _num(raw, "runHours", "run_hours"),
            "remainingHours": _num(raw, "remainingHours", "remaining_hours"),
        },
    }


def _model_dump(obj: Any) -> Optional[Dict[str, Any]]:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump")):
        try:
            res = obj.model_dump(mode="json")
            if isinstance(res, dict):
                return res
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return None


_METHOD_LABEL = {
    "iso11011_gateway_pf": "plant gateway PF leak code",
    "iso11011_pressure_decay": "discharge pressure drop vs recent history",
    "iso22096_airborne": "microphone leak-band peak (ISO 22096)",
    "iso20816_rms_only": "overall vibration RMS (ISO 20816)",
    "iso13379_catalog_rules": "vibration harmonics (ISO 13379)",
}

def _operator_explanation(
    code: str,
    name: str,
    method: str,
    guide: Optional[Dict[str, Any]],
    days_label: str,
    failing_component: Optional[str] = None,
    reasoning_summary: Optional[str] = None,
    reasoned_part_key: Optional[str] = None,
) -> Dict[str, str]:
    """Build the assistant explanation in maintenance-person language.

    Text and figures on this payload are read on the floor. Keep plant words;
    do not leave ISO codes or agent names unexplained.
    """
    from src.models.fault_bom import prompt_for_fault

    prompt = prompt_for_fault(
        code,
        failing_component,
        reasoned_part_key=reasoned_part_key,
        reasoning_summary=reasoning_summary,
    )
    method_plain = _METHOD_LABEL.get(method, method.replace("_", " ") if method else "the live rule set")
    why = reasoning_summary or prompt.get("whyShowing") or f"Shown because Agent Gamma matched {name} via {method_plain}."
    if method:
        why = f"{why} Trigger method: {method_plain}."
    root = None
    if isinstance(guide, dict):
        root = guide.get("root_cause_mechanism") or guide.get("root_cause")
    what = reasoning_summary or prompt.get("whatIsIt") or f"{name} detected on the live machine."
    payload = {
        "whatIsIt": what,
        "whyShowing": why,
        "sparePart": prompt.get("sparePart"),
        "rootCause": root or prompt.get("rootCause") or reasoning_summary or "Live sensors are outside the expected operating envelope.",
        "riskImpact": prompt.get("riskImpact")
        or f"Estimated remaining useful life is {days_label} days before a potential unplanned outage.",
    }
    if prompt.get("notThis"):
        payload["notThis"] = prompt["notThis"]
    return payload


_REPAIR_NOW = {
    "PF001": {
        "title": "Find and seal the air leak",
        "tools": "Ultrasonic leak detector, soap spray, pressure gauge",
        "time": "30–60 min",
        "steps": [
            "Walk the discharge line with an ultrasonic leak detector; soap-test unions, drain traps, and the safety valve.",
            "Do not treat this as a bearing job — vibration can stay ISO Zone A while air is leaking.",
        ],
    },
    "BPFI": {
        "title": "Stabilize the drive-end bearing",
        "tools": "Grease gun, SKF LGHP 2, infrared thermometer",
        "time": "15 min (online)",
        "steps": [
            "Clean the DE grease fitting and purge plug, then inject 15 g of SKF LGHP 2.",
            "Watch housing temperature; if it stays above 65°C, plan the SKF 6208 change in the remaining-life window.",
        ],
    },
    "BPFO": {
        "title": "Stabilize the NDE bearing",
        "tools": "Grease gun, SKF LGHP 2, infrared thermometer",
        "time": "15 min (online)",
        "steps": [
            "Relubricate the NDE housing and log temperature.",
            "Plan SKF 6208 replacement inside the remaining-life window.",
        ],
    },
    "MF002": {
        "title": "Check coupling and soft foot",
        "tools": "Feeler gauge, torque wrench",
        "time": "30 min",
        "steps": [
            "Open the coupling guard and look for shredded elastomer.",
            "Check motor feet for soft foot > 0.05 mm before a laser alignment.",
        ],
    },
    "EF001": {
        "title": "Correct supply voltage unbalance",
        "tools": "Clamp meter, infrared camera",
        "time": "20 min",
        "steps": [
            "Measure all three line voltages at the MCC and look for a loose lug or unequal single-phase load.",
            "Do not order a mechanical spare — this is a supply / winding-heat problem.",
        ],
    },
}


def _strip_step_index(step: str) -> str:
    text = str(step).strip()
    if len(text) > 2 and text[0].isdigit() and text[1] in ".)":
        return text[2:].lstrip(". ").strip()
    return text


def _repair_options_for(code: str, guide: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    key = (code or "").upper()
    now = dict(_REPAIR_NOW.get(key) or {
        "title": "Immediate field check",
        "tools": "Standard plant PPE",
        "time": "15 min",
        "steps": ["Inspect the diagnosed component against the live tiles before ordering parts."],
    })
    if isinstance(guide, dict) and guide.get("immediate_field_triage"):
        triage = str(guide["immediate_field_triage"]).strip()
        extras = [
            step for step in now["steps"][1:]
            if not ("bearing" in step.lower() and "bearing" in triage.lower())
        ]
        now["steps"] = [triage, *extras]

    options: List[Dict[str, Any]] = [{
        "title": now["title"],
        "category": "Immediate Triage",
        "urgency": "Immediate",
        "steps": now["steps"],
        "partsOrTools": now["tools"],
        "estDowntime": now["time"],
    }]

    playbook = (guide or {}).get("planned_overhaul_playbook") if isinstance(guide, dict) else None
    if isinstance(playbook, dict) and playbook.get("step_by_step_instructions"):
        hours = playbook.get("estimated_duration_hours") or 1
        tools = playbook.get("required_tools_and_materials") or []
        tool_line = ", ".join(str(t) for t in tools) if isinstance(tools, list) else str(tools)
        options.append({
            "title": playbook.get("option_title") or "Planned repair",
            "category": "Precision Repair",
            "urgency": "Scheduled",
            "steps": [_strip_step_index(s) for s in playbook["step_by_step_instructions"]],
            "partsOrTools": tool_line or now["tools"],
            "estDowntime": f"{hours:g} h" if float(hours) >= 1 else f"{int(float(hours) * 60)} min",
        })
    return options


def to_frontend_diagnosis(machine_id: str, prediction: Any) -> Dict[str, Any]:
    """Map a LangGraph PredictRULResponse onto the AI Assistant FaultDiagnosis card."""
    dump = _model_dump(prediction) or {}
    cable = dump.get("cable_check") or dump.get("cableCheck") or {}
    defect = dump.get("defect_localization") or dump.get("defectLocalization") or {}
    rul = dump.get("rul_prediction") or dump.get("rulPrediction") or {}
    elec = dump.get("electrical_health") or dump.get("electricalHealth") or {}
    thermal = dump.get("thermal_de_weathering") or dump.get("thermalDeWeathering") or {}
    cmms = dump.get("cmms_work_order") or dump.get("cmmsWorkOrder") or {}
    cable_status = str(cable.get("status") or "VALID")
    halted = cable_status != "VALID"
    missing = cable_status == "FIELD_NOT_PRESENT"
    code = str(defect.get("defect_code") or defect.get("defectCode") or "NORMAL")
    name = str(defect.get("defect_name") or defect.get("defectName") or code)
    is_defect = bool(defect) and code not in {"", "NORMAL", "NONE"}
    if isinstance(cmms, dict) and cmms.get("work_order_id"):
        mail = cmms.get("oem_mail") if isinstance(cmms.get("oem_mail"), dict) else {}
        if not mail.get("subject") or not mail.get("message"):
            from src.models.oem_mail import compose_oem_mail_from_record

            try:
                cmms["oem_mail"] = compose_oem_mail_from_record(
                    {**cmms, "machine_id": machine_id, "defect_code": code, "defect_name": name}
                ).model_dump(mode="json")
            except Exception:
                pass

    if halted:
        headline = f"Sensor Anomaly Halted: {cable_status}"
        severity = "warning" if missing else "critical"
        archetype = cable_status
    elif is_defect:
        if code.upper() == "PF001":
            headline = "Compressed-air leak (PF001) Identified"
        else:
            headline = f"{name} ({code}) Identified"
        days = rul.get("rul_days")
        severity = "critical" if isinstance(days, (int, float)) and days < 14 else "warning"
        archetype = code
    else:
        headline = "All Monitored Parameters Operating Nominally"
        severity = "good"
        archetype = "NORMAL"

    summary: List[str] = []
    if halted:
        summary.append(
            f"• Agent Alpha (Gatekeeper): HALTED ({cable.get('fault_reason') or cable_status})."
        )
    else:
        pattern = cable.get("pattern_recognition_status") or "STABLE"
        summary.append(
            f"• Agent Alpha (Gatekeeper): Validated sensor integrity across 11 checks (Pattern: {pattern})."
        )
    if elec:
        rise = thermal.get("delta_temperature_c")
        if rise is None:
            rise = thermal.get("true_thermal_rise_c")
        rise_label = f"{rise:+.1f}" if isinstance(rise, (int, float)) else "n/a"
        vuf = elec.get("voltage_unbalance_pct")
        vuf_label = f"{float(vuf):.2f}" if isinstance(vuf, (int, float)) else "n/a"
        domain = elec.get("isolated_failure_domain") or "UNKNOWN"
        summary.append(
            f"• Agent Beta (De-Weathering & PQ): Domain: {domain} | VUF: {vuf_label}% | True Thermal Rise: {rise_label}°C."
        )
    if is_defect and defect and rul:
        method = defect.get("diagnosis_method") or "iso13379"
        days = rul.get("rul_days")
        hours = rul.get("rul_operating_hours")
        days_label = f"{float(days):.1f}" if isinstance(days, (int, float)) else "n/a"
        hours_label = f"{float(hours):.0f}" if isinstance(hours, (int, float)) else "n/a"
        summary.append(
            f"• Agent Gamma (Prognostics): Diagnosed {name} via {method}. Estimated RUL is {days_label} days ({hours_label} operating hours)."
        )
    elif not halted:
        summary.append(
            "• Agent Gamma (Prognostics): All monitored mechanical & process components operating nominally."
        )
    if is_defect and cmms and cmms.get("work_order_id"):
        intel = cmms.get("sourcing_intelligence") or {}
        win = intel.get("winning_source") or intel.get("sourcing_recommendation") or "ADVISORY"
        options = intel.get("purchase_options") or []
        extra = f" {len(options)} buy options." if options else ""
        summary.append(
            f"• Agent Delta (Prescriptive Maintenance): CMMS Work Order {cmms.get('work_order_id')} drafted. Sourcing: {win}.{extra}"
        )
    elif not halted:
        summary.append(
            "• Agent Delta (Prescriptive Maintenance): No corrective maintenance actions required."
        )

    actions: List[str] = []
    guide = defect.get("expert_repair_guidance") if isinstance(defect, dict) else None
    if missing:
        actions.append("Restore the missing MQTT fields on the live topics.")
        actions.append("Verify the Product A publisher; do not infer missing values as zero.")
    elif halted:
        actions.append("Inspect accelerometer sensor cable and transducer connection.")
        actions.append("Verify NAMUR NE43 4–20 mA loop current with a multimeter.")
    elif is_defect:
        triage = guide.get("immediate_field_triage") if isinstance(guide, dict) else None
        if triage:
            actions.append(f"Field Triage: {triage}")
        if cmms.get("recommended_action"):
            actions.append(f"CMMS Action: {cmms.get('recommended_action')}")
        if not actions:
            actions.append("Inspect the diagnosed component and verify live sensor values before scheduling repair.")
    else:
        actions.append("Continue 24/7 continuous autonomous telemetry monitoring.")
        actions.append("Maintain standard lubrication schedule according to OEM guidelines.")

    explanation = None
    options: List[Dict[str, Any]] = []
    if halted:
        from src.models.fault_bom import prompt_for_fault

        prompt = prompt_for_fault("HARDWARE_CABLE_FAULT" if not missing else "SENSOR")
        reason = cable.get("fault_reason")
        explanation = {
            "whatIsIt": prompt["whatIsIt"],
            "whyShowing": (
                f"Agent Alpha halted the diagnosis ({cable_status}"
                f"{f': {reason}' if reason else ''}). IMU vibration is missing or stuck at zero "
                "while current or process conditions say the machine should be running."
            ),
            "notThis": prompt.get("notThis") or "Do not treat 0.0 mm/s as healthy. Missing or dead sensors hide real faults.",
            "sparePart": prompt.get("sparePart"),
            "rootCause": str(reason or prompt.get("rootCause") or "Cut cable, loose connector, or lost loop power."),
            "riskImpact": prompt.get("riskImpact") or "Mechanical degradation cannot be detected until the sensor path is restored.",
        }
    elif is_defect:
        days = rul.get("rul_days")
        days_label = f"{float(days):.1f}" if isinstance(days, (int, float)) else "n/a"
        explanation = _operator_explanation(
            code,
            name,
            str(defect.get("diagnosis_method") or ""),
            guide if isinstance(guide, dict) else None,
            days_label,
            str(defect.get("failing_component") or defect.get("failingComponent") or ""),
            str(defect.get("reasoning_summary") or defect.get("reasoningSummary") or "") or None,
            defect.get("reasoned_part_key") or defect.get("reasonedPartKey"),
        )
        options = _repair_options_for(code, guide if isinstance(guide, dict) else None)

    return {
        "machineId": machine_id,
        "archetype": archetype,
        "generatedAt": _now_ms(),
        "headline": headline,
        "severity": severity,
        "summary": "\n".join(summary),
        "recommendedActions": actions,
        "faultExplanation": explanation,
        "repairOptions": options,
        "pipelineDetails": dump,
    }


def _upstream_payload() -> Dict[str, Any]:
    """Shape expected by ConnectionBadge: upstream.state === 'connected'.

    Reports 'connected' if:
    - MQTT broker is actively connected (is_connected=True), OR
    - A live message has been received in the last 30 seconds (broker briefly
      dropped but data is still flowing), OR
    - Within a 15-second startup grace window (prevents a momentary Offline
      badge during the initial MQTT broker handshake on startup).
    """
    now = _now_ms()
    mqtt_ok = bool(_mqtt_service and getattr(_mqtt_service, "is_connected", False))
    last = getattr(_mqtt_service, "last_message_at_ms", None) if _mqtt_service else None
    # Treat as live if a message arrived within the last 30 seconds even if the
    # broker socket is briefly reconnecting (paho auto-reconnects in <12s).
    recently_alive = isinstance(last, int) and (now - last) < 30_000
    # 15-second startup grace: avoids showing Offline while the initial MQTT
    # TCP handshake completes after the FastAPI server starts up.
    startup_grace = bool(_upstream_since_ms and (now - _upstream_since_ms) < 15_000)
    state = "connected" if (mqtt_ok or recently_alive or startup_grace) else "offline"
    return {
        "state": state,
        "lastMessageAt": last,
        "since": _upstream_since_ms or now,
    }



def _default_map_xy(machine_id: str) -> Tuple[float, float]:
    idx = int(hashlib.md5(str(machine_id).encode()).hexdigest(), 16) % len(_FLOOR_COORDS)
    return _FLOOR_COORDS[idx]


def _machine_meta(asset: Dict[str, Any], index: int = 0) -> Optional[Dict[str, Any]]:
    mid = asset.get("id") or asset.get("machineId")
    if not mid:
        return None
    rpm = asset.get("rated_rpm") or asset.get("rpm") or 1480
    try:
        rpm = int(float(rpm))
    except (TypeError, ValueError):
        rpm = 1480
    map_x = asset.get("mapX")
    map_y = asset.get("mapY")
    if map_x is None or map_y is None:
        map_x, map_y = _FLOOR_COORDS[index % len(_FLOOR_COORDS)] if index >= 0 else _default_map_xy(str(mid))
    return {
        "id": mid,
        "name": asset.get("name", mid),
        "type": asset.get("type", "Industrial Asset"),
        "location": asset.get("location", "Factory Floor 1"),
        "ratedRpm": rpm,
        "mapX": map_x,
        "mapY": map_y,
    }


def _build_bootstrap_payload() -> Dict[str, Any]:
    """Assembles the full bootstrap snapshot for a newly-connected client."""
    machines_raw: list = []
    records: Dict[str, Any] = {}

    if _mqtt_service is None:
        return {
            "upstream": _upstream_payload(),
            "schema": SENSOR_SCHEMA,
            "machines": [],
            "records": {},
            "activeAlerts": {},
        }

    try:
        machines_raw = _mqtt_service.get_discovered_assets()
    except Exception as exc:
        logger.warning(f"bootstrap: get_discovered_assets failed: {exc}")

    machines: list[Dict[str, Any]] = []
    for idx, asset in enumerate(machines_raw):
        meta = _machine_meta(asset, index=idx)
        if not meta:
            continue
        mid = meta["id"]
        machines.append(meta)
        try:
            telem = _mqtt_service.get_asset_telemetry(mid)
        except Exception:
            telem = asset.get("telemetry") or {}
        if not isinstance(telem, dict):
            telem = {}

        has_frame = any(
            telem.get(k) is not None
            for k in ("imuAcceleration", "imu_acceleration", "rpm", "tempMotor", "temp_motor")
        )
        ts = telem.get("timestamp")
        if isinstance(ts, datetime):
            ts_ms = int(ts.timestamp() * 1000)
        elif isinstance(ts, (int, float)):
            ts_ms = int(ts)
        else:
            ts_ms = None

        latest = to_frontend_telemetry(telem, mid, ts_ms) if has_frame and ts_ms else None
        diagnosis = None
        try:
            diagnosis = getattr(_mqtt_service, "_last_diagnosis", {}).get(mid)
        except Exception:
            diagnosis = None
        records[mid] = {
            "latest": latest,
            "history": [latest] if latest else [],
            "status": None,
            "diagnosis": diagnosis,
        }

    return {
        "upstream": _upstream_payload(),
        "schema": SENSOR_SCHEMA,
        "machines": machines,
        "records": records,
        "activeAlerts": {},
    }


@sio.event
async def connect(sid: str, environ: dict, auth: Optional[dict] = None):
    logger.info(f"[SocketIO] Client connected: {sid}")
    payload = _build_bootstrap_payload()
    await sio.emit("bootstrap", payload, to=sid)
    await sio.emit("upstream:status", payload["upstream"], to=sid)


@sio.event
async def disconnect(sid: str):
    logger.info(f"[SocketIO] Client disconnected: {sid}")


async def broadcast_telemetry(machine_id: str, telemetry: Dict[str, Any]) -> None:
    now_ts = _now_ms()
    mapped = to_frontend_telemetry(telemetry, machine_id, now_ts)
    if not mapped:
        return
    await sio.emit("telemetry", {"machineId": machine_id, "telemetry": mapped})
    await sio.emit(
        "machine:status",
        {"machineId": machine_id, "reportedStatus": "online", "at": now_ts},
    )
    await sio.emit("upstream:status", _upstream_payload())


async def broadcast_diagnosis(machine_id: str, diagnosis: Dict[str, Any]) -> None:
    await sio.emit("diagnosis", {"machineId": machine_id, "diagnosis": diagnosis})


async def broadcast_alert(machine_id: str, breaches: list, activity: Optional[Dict] = None) -> None:
    payload: Dict[str, Any] = {"machineId": machine_id, "breaches": breaches}
    if activity:
        payload["activity"] = activity
    await sio.emit("alert", payload)


async def broadcast_upstream() -> None:
    await sio.emit("upstream:status", _upstream_payload())


async def broadcast_machines(assets: list) -> None:
    machines = []
    for idx, asset in enumerate(assets or []):
        meta = _machine_meta(asset or {}, index=idx)
        if meta:
            machines.append(meta)
    await sio.emit("machines:update", machines)


def emit_telemetry_sync(machine_id: str, telemetry: Dict[str, Any]) -> None:
    if _main_loop is None or not _main_loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(broadcast_telemetry(machine_id, telemetry), _main_loop)


def emit_diagnosis_sync(machine_id: str, diagnosis: Dict[str, Any]) -> None:
    if _main_loop is None or not _main_loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(broadcast_diagnosis(machine_id, diagnosis), _main_loop)


def emit_alert_sync(machine_id: str, breaches: list, activity: Optional[Dict] = None) -> None:
    if _main_loop is None or not _main_loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(broadcast_alert(machine_id, breaches, activity), _main_loop)


def emit_machines_sync(assets: list) -> None:
    if _main_loop is None or not _main_loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(broadcast_machines(assets), _main_loop)


def emit_upstream_sync() -> None:
    if _main_loop is None or not _main_loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(broadcast_upstream(), _main_loop)
