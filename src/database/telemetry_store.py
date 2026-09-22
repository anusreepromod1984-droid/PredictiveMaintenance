"""
Persist MQTT TelemetryFrame rows into public.telemetry_reading.

Creates the table on startup if it is missing. Insert failures are logged and
swallowed so a down database never blocks Alpha→Delta.
Waveforms are not stored (too large for every MQTT tick).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from src.config import settings
from src.database.connection import engine
from src.schemas.telemetry import TelemetryFrame
from src.utils.logger import get_logger

logger = get_logger("Database.TelemetryStore")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS public.telemetry_reading (
    id                  BIGSERIAL PRIMARY KEY,
    "machineId"         TEXT NOT NULL,
    "timestamp"         TIMESTAMPTZ NOT NULL,
    "receivedAt"        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "mqttTopic"         TEXT,
    "imuAcceleration"   DOUBLE PRECISION,
    rpm                 DOUBLE PRECISION,
    "tempMotor"         DOUBLE PRECISION,
    "tempCompressor"    DOUBLE PRECISION,
    "tempAmbient"       DOUBLE PRECISION,
    humidity            DOUBLE PRECISION,
    "emIr"              DOUBLE PRECISION,
    "emIy"              DOUBLE PRECISION,
    "emIb"              DOUBLE PRECISION,
    "emVr"              DOUBLE PRECISION,
    "emVy"              DOUBLE PRECISION,
    "emVb"              DOUBLE PRECISION,
    "emMachineLoad"     DOUBLE PRECISION,
    "emVoltageImbalance" DOUBLE PRECISION,
    "emPower"           DOUBLE PRECISION,
    "emEnergy"          DOUBLE PRECISION,
    "emAveragePowerFactor" DOUBLE PRECISION,
    "emThdVr"           DOUBLE PRECISION,
    "emThdVy"           DOUBLE PRECISION,
    "emThdVb"           DOUBLE PRECISION,
    "emFrequency"       DOUBLE PRECISION,
    "emFrequencyDeviation" DOUBLE PRECISION,
    "soundLevel"        DOUBLE PRECISION,
    pressure            DOUBLE PRECISION,
    dust                DOUBLE PRECISION,
    "magRoll"           DOUBLE PRECISION,
    "magPitch"          DOUBLE PRECISION,
    "magYaw"            DOUBLE PRECISION,
    "runHours"          DOUBLE PRECISION,
    "remainingHours"    DOUBLE PRECISION,
    "sensorOk"          BOOLEAN,
    "vibrationHarmonics" JSONB,
    "micHarmonics"      JSONB,
    "motorFaults"       JSONB,
    "sourceKeys"        JSONB,
    "missingBlocks"     JSONB
);
"""

_INDEXES = [
    """CREATE INDEX IF NOT EXISTS telemetry_reading_machine_ts_idx
       ON public.telemetry_reading ("machineId", "timestamp" DESC);""",
    """CREATE INDEX IF NOT EXISTS telemetry_reading_received_idx
       ON public.telemetry_reading ("receivedAt" DESC);""",
]

_CREATE_AGENT_SNAPSHOT = """
CREATE TABLE IF NOT EXISTS public.agent_snapshot (
    id                      BIGSERIAL PRIMARY KEY,
    "traceId"               TEXT NOT NULL,
    "machineId"             TEXT NOT NULL,
    "timestamp"             TIMESTAMPTZ NOT NULL,
    "receivedAt"            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "patternStatus"         TEXT,
    "isolatedFailureDomain" TEXT,
    "imuAcceleration"       DOUBLE PRECISION,
    "runHours"              DOUBLE PRECISION,
    "tempMotor"             DOUBLE PRECISION,
    "tempCompressor"        DOUBLE PRECISION,
    "emEnergy"              DOUBLE PRECISION,
    "emPower"               DOUBLE PRECISION,
    pressure                DOUBLE PRECISION,
    "soundLevel"            DOUBLE PRECISION,
    "defectCode"            TEXT,
    "diagnosisMethod"       TEXT,
    "predictionMode"        TEXT,
    "rulOperatingHours"     DOUBLE PRECISION,
    "rulDays"               DOUBLE PRECISION,
    "rulMethod"             TEXT,
    "healthScore"           DOUBLE PRECISION,
    "healthStatus"          TEXT,
    "skipReason"            TEXT,
    "sourceKeys"            JSONB,
    inputs                  JSONB NOT NULL,
    outputs                 JSONB NOT NULL
);
"""

_AGENT_SNAPSHOT_INDEXES = [
    """CREATE UNIQUE INDEX IF NOT EXISTS agent_snapshot_trace_idx
       ON public.agent_snapshot ("traceId");""",
    """CREATE INDEX IF NOT EXISTS agent_snapshot_machine_ts_idx
       ON public.agent_snapshot ("machineId", "timestamp" DESC);""",
]

_INSERT_AGENT_SNAPSHOT = """
INSERT INTO public.agent_snapshot (
    "traceId", "machineId", "timestamp", "receivedAt",
    "patternStatus", "isolatedFailureDomain",
    "imuAcceleration", "runHours", "tempMotor", "tempCompressor",
    "emEnergy", "emPower", pressure, "soundLevel",
    "defectCode", "diagnosisMethod", "predictionMode",
    "rulOperatingHours", "rulDays", "rulMethod",
    "healthScore", "healthStatus", "skipReason",
    "sourceKeys", inputs, outputs
)
SELECT
    :traceId, :machineId, :timestamp, :receivedAt,
    :patternStatus, :isolatedFailureDomain,
    :imuAcceleration, :runHours, :tempMotor, :tempCompressor,
    :emEnergy, :emPower, :pressure, :soundLevel,
    :defectCode, :diagnosisMethod, :predictionMode,
    :rulOperatingHours, :rulDays, :rulMethod,
    :healthScore, :healthStatus, :skipReason,
    CAST(:sourceKeys AS jsonb), CAST(:inputs AS jsonb), CAST(:outputs AS jsonb)
WHERE NOT EXISTS (
    SELECT 1 FROM public.agent_snapshot s WHERE s."traceId" = :traceId
)
"""

_OPTIONAL_COLUMNS = [
    ('"mqttTopic"', "TEXT"),
    ('"tempAmbient"', "DOUBLE PRECISION"),
    ('"emAveragePowerFactor"', "DOUBLE PRECISION"),
    ('"emEnergy"', "DOUBLE PRECISION"),
    ('"emThdVr"', "DOUBLE PRECISION"),
    ('"emThdVy"', "DOUBLE PRECISION"),
    ('"emThdVb"', "DOUBLE PRECISION"),
    ('"emFrequency"', "DOUBLE PRECISION"),
    ('"emFrequencyDeviation"', "DOUBLE PRECISION"),
    ('"soundLevel"', "DOUBLE PRECISION"),
    ('pressure', "DOUBLE PRECISION"),
    ('dust', "DOUBLE PRECISION"),
    ('"magRoll"', "DOUBLE PRECISION"),
    ('"magPitch"', "DOUBLE PRECISION"),
    ('"magYaw"', "DOUBLE PRECISION"),
    ('"runHours"', "DOUBLE PRECISION"),
    ('"remainingHours"', "DOUBLE PRECISION"),
    ('"sensorOk"', "BOOLEAN"),
    ('"vibrationHarmonics"', "JSONB"),
    ('"micHarmonics"', "JSONB"),
    ('"motorFaults"', "JSONB"),
    ('"sourceKeys"', "JSONB"),
    ('"missingBlocks"', "JSONB"),
]

_INSERT = """
INSERT INTO public.telemetry_reading (
    "machineId", "timestamp", "receivedAt", "mqttTopic",
    "imuAcceleration", rpm, "tempMotor", "tempCompressor", "tempAmbient", humidity,
    "emIr", "emIy", "emIb", "emVr", "emVy", "emVb",
    "emMachineLoad", "emVoltageImbalance", "emPower", "emEnergy", "emAveragePowerFactor",
    "emThdVr", "emThdVy", "emThdVb", "emFrequency", "emFrequencyDeviation",
    "soundLevel", pressure, dust, "magRoll", "magPitch", "magYaw",
    "runHours", "remainingHours", "sensorOk",
    "vibrationHarmonics", "micHarmonics", "motorFaults", "sourceKeys", "missingBlocks"
)
SELECT
    :machineId, :timestamp, :receivedAt, :mqttTopic,
    :imuAcceleration, :rpm, :tempMotor, :tempCompressor, :tempAmbient, :humidity,
    :emIr, :emIy, :emIb, :emVr, :emVy, :emVb,
    :emMachineLoad, :emVoltageImbalance, :emPower, :emEnergy, :emAveragePowerFactor,
    :emThdVr, :emThdVy, :emThdVb, :emFrequency, :emFrequencyDeviation,
    :soundLevel, :pressure, :dust, :magRoll, :magPitch, :magYaw,
    :runHours, :remainingHours, :sensorOk,
    CAST(:vibrationHarmonics AS jsonb), CAST(:micHarmonics AS jsonb),
    CAST(:motorFaults AS jsonb), CAST(:sourceKeys AS jsonb), CAST(:missingBlocks AS jsonb)
WHERE NOT EXISTS (
    SELECT 1 FROM public.telemetry_reading t
    WHERE t."machineId" = :machineId AND t."timestamp" = :timestamp
)
"""

_last_persist_error_at = 0.0
_schema_ready = False


def _aware(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _harmonics_json(peaks: Any) -> str:
    rows = []
    for peak in peaks or []:
        if hasattr(peak, "frequency"):
            rows.append({"frequency": float(peak.frequency), "amplitude": float(peak.amplitude)})
        elif isinstance(peak, dict):
            rows.append({
                "frequency": float(peak.get("frequency") or 0.0),
                "amplitude": float(peak.get("amplitude") or 0.0),
            })
    return json.dumps(rows)


def frame_to_db_params(frame: TelemetryFrame, mqtt_topic: Optional[str] = None) -> Dict[str, Any]:
    """Map a live frame to telemetry_reading bind params. No waveform columns."""
    received = datetime.now(timezone.utc)
    return {
        "machineId": frame.machine_id,
        "timestamp": _aware(frame.timestamp),
        "receivedAt": received,
        "mqttTopic": mqtt_topic,
        "imuAcceleration": float(frame.imu_acceleration),
        "rpm": float(frame.rpm),
        "tempMotor": float(frame.temp_motor),
        "tempCompressor": float(frame.temp_compressor),
        "tempAmbient": float(frame.temp_ambient),
        "humidity": float(frame.humidity),
        "emIr": float(frame.em_ir),
        "emIy": float(frame.em_iy),
        "emIb": float(frame.em_ib),
        "emVr": float(frame.em_vr),
        "emVy": float(frame.em_vy),
        "emVb": float(frame.em_vb),
        "emMachineLoad": float(frame.em_machine_load),
        "emVoltageImbalance": float(frame.em_voltage_imbalance),
        "emPower": float(frame.em_power),
        "emEnergy": frame.em_energy,
        "emAveragePowerFactor": float(frame.em_power_factor),
        "emThdVr": float(frame.em_thd_v),
        "emThdVy": float(frame.em_thd_vy),
        "emThdVb": float(frame.em_thd_vb),
        "emFrequency": float(frame.em_frequency),
        "emFrequencyDeviation": float(frame.em_frequency_deviation),
        "soundLevel": float(frame.sound_level),
        "pressure": float(frame.pressure),
        "dust": frame.dust,
        "magRoll": float(frame.mag_roll),
        "magPitch": float(frame.mag_pitch),
        "magYaw": float(frame.mag_yaw),
        "runHours": float(frame.run_hours),
        "remainingHours": frame.remaining_hours,
        "sensorOk": True,
        "vibrationHarmonics": _harmonics_json(frame.vibration_harmonics),
        "micHarmonics": _harmonics_json(frame.mic_harmonics),
        "motorFaults": json.dumps(frame.motor_faults),
        "sourceKeys": json.dumps(frame.source_keys),
        "missingBlocks": json.dumps(frame.missing_blocks),
    }


def ensure_telemetry_schema() -> bool:
    """Create telemetry_reading + agent_snapshot. Returns False if DB is down."""
    global _schema_ready
    try:
        with engine.begin() as conn:
            conn.execute(text(_CREATE_TABLE))
            for name, col_type in _OPTIONAL_COLUMNS:
                conn.execute(text(
                    f"ALTER TABLE public.telemetry_reading ADD COLUMN IF NOT EXISTS {name} {col_type}"
                ))
            for index_sql in _INDEXES:
                conn.execute(text(index_sql))
            conn.execute(text(_CREATE_AGENT_SNAPSHOT))
            for index_sql in _AGENT_SNAPSHOT_INDEXES:
                conn.execute(text(index_sql))
        _schema_ready = True
        logger.info("[Postgres] public.telemetry_reading and public.agent_snapshot are ready")
        return True
    except Exception as exc:
        _schema_ready = False
        logger.warning("[Postgres] Could not ensure telemetry schema: %s", exc)
        return False


def persist_mqtt_frame(frame: TelemetryFrame, mqtt_topic: Optional[str] = None) -> bool:
    """Insert one MQTT frame. Never raises. Returns True when the row was written."""
    global _last_persist_error_at, _schema_ready
    if not settings.TELEMETRY_PERSIST_ENABLED:
        return False
    if mqtt_topic and mqtt_topic.rstrip("/") in {
        settings.MQTT_TOPIC_ALERTS,
        "pdm/alerts",
        "pdm/predictions",
    }:
        return False
    if not _schema_ready:
        ensure_telemetry_schema()
    params = frame_to_db_params(frame, mqtt_topic=mqtt_topic)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(_INSERT), params)
        return bool(getattr(result, "rowcount", 0))
    except Exception as exc:
        now = time.monotonic()
        if now - _last_persist_error_at >= 60.0:
            logger.warning("[Postgres] MQTT persist failed (will retry on next packet): %s", exc)
            _last_persist_error_at = now
        _schema_ready = False
        return False


def list_latest_assets_from_db() -> List[Dict[str, Any]]:
    """One row per machineId that has actually been persisted from MQTT."""
    if not settings.TELEMETRY_PERSIST_ENABLED:
        return []
    sql = text(
        """
        SELECT DISTINCT ON ("machineId")
            "machineId", "timestamp", "mqttTopic",
            "imuAcceleration", rpm, "tempMotor", "tempCompressor", "tempAmbient",
            humidity, pressure, dust, "soundLevel", "runHours",
            "magRoll", "magPitch", "magYaw",
            "emIr", "emIy", "emIb", "emVr", "emVy", "emVb",
            "emMachineLoad", "emVoltageImbalance", "emPower", "emEnergy",
            "emAveragePowerFactor", "emThdVr", "emThdVy", "emThdVb",
            "emFrequency", "emFrequencyDeviation",
            "vibrationHarmonics", "micHarmonics", "motorFaults",
            "sourceKeys", "missingBlocks"
        FROM public.telemetry_reading
        ORDER BY "machineId", "timestamp" DESC
        """
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql)
            assets = []
            for row in rows:
                rec = dict(row._mapping)
                mid = rec.get("machineId")
                if not mid:
                    continue
                ts = rec.get("timestamp")
                ts_ms = int(ts.timestamp() * 1000) if hasattr(ts, "timestamp") else None
                assets.append({
                    "id": mid,
                    "name": str(mid).replace("_", " ").title(),
                    "type": "Industrial Asset",
                    "status": "STANDBY",
                    "location": "Factory Floor 1",
                    "source": "mqtt_history",
                    "last_seen": ts.isoformat() if hasattr(ts, "isoformat") else None,
                    "rpm": rec.get("rpm"),
                    "telemetry": {**{k: v for k, v in rec.items() if k != "timestamp"}, "timestamp": ts_ms},
                })
            return assets
    except Exception as exc:
        logger.debug("[Postgres] list_latest_assets_from_db: %s", exc)
        return []


def list_asset_telemetry_from_db(machine_id: str, limit: int = 600) -> List[Dict[str, Any]]:
    """Return real persisted Product A/B/C snapshots in chronological order."""
    if not settings.TELEMETRY_PERSIST_ENABLED:
        return []
    sql = text(
        """
        SELECT
            "machineId", "timestamp", "mqttTopic",
            "imuAcceleration", rpm, "tempMotor", "tempCompressor", "tempAmbient",
            humidity, pressure, dust, "soundLevel", "runHours", "remainingHours",
            "magRoll", "magPitch", "magYaw",
            "emIr", "emIy", "emIb", "emVr", "emVy", "emVb",
            "emMachineLoad", "emVoltageImbalance", "emPower", "emEnergy",
            "emAveragePowerFactor", "emThdVr", "emThdVy", "emThdVb",
            "emFrequency", "emFrequencyDeviation",
            "vibrationHarmonics", "micHarmonics", "motorFaults",
            "sourceKeys", "missingBlocks"
        FROM public.telemetry_reading
        WHERE "machineId" = :machine_id
        ORDER BY "timestamp" DESC
        LIMIT :limit
        """
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql, {"machine_id": machine_id, "limit": max(1, min(limit, 5000))})
            result: List[Dict[str, Any]] = []
            for row in rows:
                rec = dict(row._mapping)
                ts = rec.get("timestamp")
                rec["timestamp"] = int(ts.timestamp() * 1000) if hasattr(ts, "timestamp") else ts
                result.append(rec)
            result.reverse()
            return result
    except Exception as exc:
        logger.debug("[Postgres] list_asset_telemetry_from_db: %s", exc)
        return []


def latest_plausible_imu_from_db(
    machine_id: str,
    *,
    max_mm_s: float = 40.0,
    hold_seconds: float = 300.0,
) -> Optional[float]:
    """Last IMU-Acceleration RMS from pdm/vibration (or a stamped frame) within the hold window."""
    if not settings.TELEMETRY_PERSIST_ENABLED:
        return None
    sql = text(
        """
        SELECT "imuAcceleration"
        FROM public.telemetry_reading
        WHERE "machineId" = :machine_id
          AND "timestamp" > NOW() - (:hold_seconds * INTERVAL '1 second')
          AND "imuAcceleration" IS NOT NULL
          AND "imuAcceleration" > 0.01
          AND "imuAcceleration" <= :max_mm_s
          AND (
            "mqttTopic" = 'pdm/vibration'
            OR "sourceKeys" ? 'imuAcceleration'
          )
        ORDER BY "timestamp" DESC
        LIMIT 1
        """
    )
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sql,
                {
                    "machine_id": machine_id,
                    "hold_seconds": max(30.0, float(hold_seconds)),
                    "max_mm_s": float(max_mm_s),
                },
            ).first()
        if not row:
            return None
        value = row._mapping.get("imuAcceleration")
        return float(value) if isinstance(value, (int, float)) else None
    except Exception as exc:
        logger.debug("[Postgres] latest_plausible_imu_from_db: %s", exc)
        return None


def list_recent_energy_samples(machine_id: str, minutes: int = 12) -> List[Tuple[float, float]]:
    """One (unix_ts, kWh) point per minute so power can be estimated after a restart."""
    if not settings.TELEMETRY_PERSIST_ENABLED:
        return []
    sql = text(
        """
        SELECT DISTINCT ON (date_trunc('minute', "timestamp"))
            "timestamp", "emEnergy"
        FROM public.telemetry_reading
        WHERE "machineId" = :machine_id
          AND "timestamp" > NOW() - (:minutes * INTERVAL '1 minute')
          AND "emEnergy" IS NOT NULL
        ORDER BY date_trunc('minute', "timestamp") ASC, "timestamp" ASC
        """
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql, {"machine_id": machine_id, "minutes": max(3, min(int(minutes), 60))})
            samples: List[Tuple[float, float]] = []
            for row in rows:
                ts = row._mapping.get("timestamp")
                energy = row._mapping.get("emEnergy")
                if ts is None or energy is None:
                    continue
                wall = float(ts.timestamp()) if hasattr(ts, "timestamp") else float(ts)
                samples.append((wall, float(energy)))
            return samples
    except Exception as exc:
        logger.debug("[Postgres] list_recent_energy_samples: %s", exc)
        return []


_WAVEFORM_EXCLUDE = {
    "waveform", "waveformX", "waveformY", "waveformZ",
    "waveform_x", "waveform_y", "waveform_z",
}


def agent_inputs_from_frame(frame: TelemetryFrame) -> Dict[str, Any]:
    """Canonical scalars the agents actually consumed. Waveforms are omitted (too large)."""
    return frame.model_dump(mode="json", by_alias=True, exclude=_WAVEFORM_EXCLUDE)


def _model_json(obj: Any) -> Optional[Dict[str, Any]]:
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", by_alias=True)
    if isinstance(obj, dict):
        return obj
    return None


def agent_snapshot_params(frame: TelemetryFrame, response: Any) -> Dict[str, Any]:
    """Map one Alpha→Delta run to agent_snapshot bind params."""
    cable = getattr(response, "cable_check", None)
    thermal = getattr(response, "thermal_de_weathering", None)
    electrical = getattr(response, "electrical_health", None)
    defect = getattr(response, "defect_localization", None)
    rul = getattr(response, "rul_prediction", None)
    cmms = getattr(response, "cmms_work_order", None)
    received = datetime.now(timezone.utc)
    ts = getattr(response, "timestamp", None) or frame.timestamp
    outputs = {
        "cableCheck": _model_json(cable),
        "thermal": _model_json(thermal),
        "electrical": _model_json(electrical),
        "defect": _model_json(defect),
        "rul": _model_json(rul),
        "cmmsWorkOrderId": getattr(cmms, "work_order_id", None) if cmms is not None else None,
        "isoVibrationZone": getattr(response, "iso_vibration_zone", None),
    }
    return {
        "traceId": getattr(response, "trace_id", None) or f"trc_local_{int(time.time())}",
        "machineId": frame.machine_id,
        "timestamp": _aware(ts) if isinstance(ts, datetime) else received,
        "receivedAt": received,
        "patternStatus": getattr(cable, "pattern_recognition_status", None) if cable is not None else None,
        "isolatedFailureDomain": getattr(electrical, "isolated_failure_domain", None) if electrical is not None else None,
        "imuAcceleration": float(frame.imu_acceleration) if frame.field_was_sent("imuAcceleration") else None,
        "runHours": float(frame.run_hours),
        "tempMotor": float(frame.temp_motor) if frame.field_was_sent("tempMotor") else None,
        "tempCompressor": float(frame.temp_compressor) if frame.field_was_sent("tempCompressor") else None,
        "emEnergy": float(frame.em_energy) if frame.em_energy is not None and frame.field_was_sent("emEnergy") else None,
        "emPower": float(frame.em_power) if frame.field_was_sent("emPower") else None,
        "pressure": float(frame.pressure) if frame.field_was_sent("pressure") else None,
        "soundLevel": float(frame.sound_level) if frame.field_was_sent("soundLevel") else None,
        "defectCode": getattr(defect, "defect_code", None) if defect is not None else None,
        "diagnosisMethod": getattr(defect, "diagnosis_method", None) if defect is not None else None,
        "predictionMode": getattr(defect, "prediction_mode", None) if defect is not None else None,
        "rulOperatingHours": getattr(rul, "rul_operating_hours", None) if rul is not None else None,
        "rulDays": getattr(rul, "rul_days", None) if rul is not None else None,
        "rulMethod": getattr(rul, "method", None) if rul is not None else None,
        "healthScore": getattr(response, "overall_health_score", None),
        "healthStatus": getattr(response, "overall_health_status", None),
        "skipReason": getattr(response, "skip_reason", None),
        "sourceKeys": json.dumps(frame.source_keys or []),
        "inputs": json.dumps(agent_inputs_from_frame(frame)),
        "outputs": json.dumps(outputs),
    }


def persist_agent_snapshot(frame: TelemetryFrame, response: Any) -> bool:
    """Store the frame agents consumed plus Alpha/Beta/Gamma/Delta outputs. Never raises."""
    global _last_persist_error_at, _schema_ready
    if not settings.TELEMETRY_PERSIST_ENABLED:
        return False
    if not _schema_ready:
        ensure_telemetry_schema()
    params = agent_snapshot_params(frame, response)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(_INSERT_AGENT_SNAPSHOT), params)
        return bool(getattr(result, "rowcount", 0))
    except Exception as exc:
        now = time.monotonic()
        if now - _last_persist_error_at >= 60.0:
            logger.warning("[Postgres] agent_snapshot persist failed (will retry on next run): %s", exc)
            _last_persist_error_at = now
        _schema_ready = False
        return False


def list_agent_snapshots(machine_id: str, limit: int = 48) -> List[Dict[str, Any]]:
    """Newest-first agent runs for RUL / trend math. Empty if Postgres is down."""
    if not settings.TELEMETRY_PERSIST_ENABLED:
        return []
    sql = text(
        """
        SELECT
            "traceId", "machineId", "timestamp",
            "patternStatus", "isolatedFailureDomain",
            "imuAcceleration", "runHours", "tempMotor", "tempCompressor",
            "emEnergy", "emPower", pressure, "soundLevel",
            "defectCode", "rulOperatingHours", "rulDays", "rulMethod",
            "healthScore", "healthStatus", "skipReason",
            "sourceKeys", inputs, outputs
        FROM public.agent_snapshot
        WHERE "machineId" = :machine_id
        ORDER BY "timestamp" DESC
        LIMIT :limit
        """
    )
    try:
        if not _schema_ready:
            ensure_telemetry_schema()
        with engine.connect() as conn:
            rows = conn.execute(sql, {"machine_id": machine_id, "limit": max(1, min(int(limit), 500))})
            result: List[Dict[str, Any]] = []
            for row in rows:
                rec = dict(row._mapping)
                ts = rec.get("timestamp")
                rec["timestamp"] = int(ts.timestamp() * 1000) if hasattr(ts, "timestamp") else ts
                result.append(rec)
            return result
    except Exception as exc:
        logger.debug("[Postgres] list_agent_snapshots: %s", exc)
        return []
