"""Gbotz PDM Sensor API client — POST /api/ai/query for persisted history."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from src.config import settings
from src.schemas.telemetry import HarmonicPeak, TelemetryFrame
from src.utils.logger import get_logger

logger = get_logger("Services.Gbotz")


class GbotzClientError(RuntimeError):
    pass


def api_key_configured() -> bool:
    return bool((settings.AI_SERVICE_API_KEY or "").strip())


def _headers() -> Dict[str, str]:
    key = (settings.AI_SERVICE_API_KEY or "").strip()
    if not key:
        raise GbotzClientError("AI_SERVICE_API_KEY is empty in .env")
    return {"Content-Type": "application/json", "X-API-Key": key}


def _base() -> str:
    return (settings.AI_SERVICE_BASE_URL or "http://168.144.37.186:4000").rstrip("/")


def _url(path: str) -> str:
    root = _base()
    if path.startswith("/api/") and root.endswith("/api"):
        return f"{root}{path[4:]}"
    if not path.startswith("/"):
        path = "/" + path
    return f"{root}{path}"


def get_capabilities() -> Dict[str, Any]:
    with httpx.Client(timeout=settings.AI_SERVICE_TIMEOUT_S) as client:
        response = client.get(_url("/api/ai/query/capabilities"), headers=_headers())
    if response.status_code >= 400:
        raise GbotzClientError(f"capabilities {response.status_code}: {response.text[:300]}")
    return response.json()


def query(body: Dict[str, Any]) -> Dict[str, Any]:
    with httpx.Client(timeout=settings.AI_SERVICE_TIMEOUT_S) as client:
        response = client.post(_url("/api/ai/query"), headers=_headers(), json=body)
    if response.status_code >= 400:
        raise GbotzClientError(f"ai/query {response.status_code}: {response.text[:400]}")
    return response.json()


def _pick(row: Dict[str, Any], *paths: str, default: Any = None) -> Any:
    for path in paths:
        if path in row and row[path] is not None:
            return row[path]
        cur: Any = row
        found = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                found = False
                break
        if found and cur is not None:
            return cur
    return default


def _num(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _harmonics(raw: Any) -> List[HarmonicPeak]:
    peaks = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        peaks.append(HarmonicPeak(
            frequency=_num(item.get("frequency") or item.get("freq")),
            amplitude=_num(item.get("amplitude") or item.get("amp")),
        ))
    return peaks


def gbotz_row_to_frame(row: Dict[str, Any]) -> Optional[TelemetryFrame]:
    """Map a Gbotz Telemetry object (nested or flat) to TelemetryFrame. None if RMS missing."""
    if not isinstance(row, dict):
        return None
    imu = _pick(row, "imuAcceleration", "imu_acceleration")
    if imu is None:
        return None

    ts = row.get("timestamp")
    if isinstance(ts, (int, float)):
        timestamp = datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc)
    elif isinstance(ts, str) and ts:
        timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    else:
        timestamp = datetime.now(timezone.utc)

    vib = row.get("vibration") if isinstance(row.get("vibration"), dict) else {}
    mic = row.get("microphone") if isinstance(row.get("microphone"), dict) else {}
    harmonics = _harmonics(_pick(row, "vibration.harmonics", "vibrationHarmonics") or vib.get("harmonics"))
    mic_harmonics = _harmonics(_pick(row, "microphone.harmonics", "micHarmonics") or mic.get("harmonics"))

    remaining = _pick(row, "runtime.remainingHours", "remainingHours")

    return TelemetryFrame(
        machineId=str(row.get("machineId") or row.get("machine_id") or "unknown"),
        timestamp=timestamp,
        imuAcceleration=_num(imu),
        rpm=_num(_pick(row, "rpm"), 0.0),
        tempMotor=_num(_pick(row, "temperature.motor", "tempMotor")),
        tempCompressor=_num(_pick(row, "temperature.compressor", "tempCompressor")),
        tempAmbient=_num(_pick(row, "tempAmbient"), 25.0),
        humidity=_num(_pick(row, "humidity"), 45.0),
        emIr=_num(_pick(row, "energyMeter.Ir", "emIr")),
        emIy=_num(_pick(row, "energyMeter.Iy", "emIy")),
        emIb=_num(_pick(row, "energyMeter.Ib", "emIb")),
        emVr=_num(_pick(row, "energyMeter.Vr", "emVr")),
        emVy=_num(_pick(row, "energyMeter.Vy", "emVy")),
        emVb=_num(_pick(row, "energyMeter.Vb", "emVb")),
        emMachineLoad=_num(_pick(row, "energyMeter.machineLoad", "emMachineLoad")),
        emVoltageImbalance=_num(_pick(row, "energyMeter.voltageImbalance", "emVoltageImbalance")),
        emPower=_num(_pick(row, "energyMeter.power", "emPower")),
        emPowerFactor=_num(_pick(row, "energyMeter.averagePowerFactor", "emAveragePowerFactor"), 0.92),
        emEnergy=_num(_pick(row, "energyMeter.energy", "emEnergy")) if _pick(row, "energyMeter.energy", "emEnergy") is not None else None,
        emThdVr=_num(_pick(row, "energyMeter.thdVr", "emThdVr"), 1.8),
        soundLevel=_num(_pick(row, "microphone.soundLevel", "soundLevel")),
        magRoll=_num(_pick(row, "magnetometer.roll", "magRoll")),
        magPitch=_num(_pick(row, "magnetometer.pitch", "magPitch")),
        magYaw=_num(_pick(row, "magnetometer.yaw", "magYaw")),
        runHours=_num(_pick(row, "runtime.machineRunHours", "runHours")),
        remainingHours=None if remaining is None else _num(remaining),
        vibrationHarmonics=harmonics,
        micHarmonics=mic_harmonics,
        plcParam12Status=0 if _pick(row, "sensorStatus.ok", default=True) else 1,
    )


def fetch_history_rows(
    *,
    duration: str = "14d",
    max_points: int = 300,
    machine_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """One history query. Caller chunks longer ranges if the server rejects the window."""
    body: Dict[str, Any] = {
        "include": ["history"],
        "history": {
            "source": "db",
            "duration": duration,
            "maxPoints": max_points,
        },
        "fields": [
            "rpm",
            "imuAcceleration",
            "humidity",
            "temperature.motor",
            "temperature.compressor",
            "energyMeter.Ir",
            "energyMeter.Iy",
            "energyMeter.Ib",
            "energyMeter.Vr",
            "energyMeter.Vy",
            "energyMeter.Vb",
            "energyMeter.machineLoad",
            "energyMeter.voltageImbalance",
            "energyMeter.power",
            "energyMeter.averagePowerFactor",
            "energyMeter.thdVr",
            "microphone.soundLevel",
            "runtime.machineRunHours",
            "sensorStatus.ok",
        ],
    }
    if machine_ids:
        body["machineIds"] = machine_ids
    payload = query(body)
    rows: List[Dict[str, Any]] = []
    for machine in payload.get("machines") or []:
        history = machine.get("history") or {}
        mid = machine.get("machineId")
        for row in history.get("rows") or []:
            if mid and not row.get("machineId"):
                row = {**row, "machineId": mid}
            rows.append(row)
    return rows
