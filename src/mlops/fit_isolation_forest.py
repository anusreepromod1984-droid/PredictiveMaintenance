"""Fit Isolation Forest on running-healthy telemetry_reading rows. ISO 20816 stays the alarm."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from src.config import settings
from src.mlops.store import registry_path, save_registry_json
from src.utils.logger import get_logger

logger = get_logger("MLops.IsolationForest")

FEATURE_KEYS = [
    "imuAcceleration",
    "deltaTemp",
    "emVoltageImbalance",
    "emMachineLoad",
    "humidity",
]


def row_to_vector(row: Dict[str, Any]) -> List[float]:
    t_m = float(row.get("tempMotor") or 0.0)
    t_a = float(row.get("tempAmbient") if row.get("tempAmbient") is not None else 25.0)
    return [
        float(row.get("imuAcceleration") or 0.0),
        t_m - t_a,
        float(row.get("emVoltageImbalance") or 0.0),
        float(row.get("emMachineLoad") or 0.0),
        float(row.get("humidity") or 45.0),
    ]


def is_running_healthy(row: Dict[str, Any]) -> bool:
    """Train only on duty that looks like NORMAL — not idle, not already in ISO zone C/D."""
    rpm = float(row.get("rpm") or 0.0)
    if rpm < settings.BASELINE_MIN_RPM:
        return False
    if row.get("sensorOk") is False:
        return False
    rms = float(row.get("imuAcceleration") or 0.0)
    if rms <= 0.0 or rms >= settings.VIB_WARNING_MAX:
        return False
    vuf = float(row.get("emVoltageImbalance") or 0.0)
    if vuf >= settings.VUF_DANGER_MIN:
        return False
    load = float(row.get("emMachineLoad") or 0.0)
    if load < 5.0:
        return False
    return True


def fetch_running_healthy_rows(limit: int = 20000) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    from src.database.connection import engine

    query = text("""
        SELECT
            "machineId", "timestamp", "imuAcceleration", rpm,
            "tempMotor", "tempAmbient", humidity,
            "emVoltageImbalance", "emMachineLoad", "sensorOk"
        FROM public.telemetry_reading
        WHERE rpm IS NOT NULL
          AND rpm >= :min_rpm
          AND "imuAcceleration" IS NOT NULL
        ORDER BY "timestamp" DESC
        LIMIT :limit
    """)
    try:
        with engine.connect() as conn:
            result = conn.execute(query, {"min_rpm": settings.BASELINE_MIN_RPM, "limit": limit})
            return [dict(row._mapping) for row in result]
    except Exception as exc:
        logger.warning("[Baseline] Could not read telemetry_reading: %s", exc)
        return []


def count_running_rows() -> Optional[int]:
    from sqlalchemy import text
    from src.database.connection import engine

    try:
        with engine.connect() as conn:
            n = conn.execute(text(
                'SELECT COUNT(*) FROM public.telemetry_reading WHERE rpm >= :min_rpm'
            ), {"min_rpm": settings.BASELINE_MIN_RPM}).scalar()
            return int(n or 0)
    except Exception:
        return None


def fit_isolation_forest(experimental: bool = True) -> Dict[str, Any]:
    rows = [r for r in fetch_running_healthy_rows() if is_running_healthy(r)]
    minimum = settings.MIN_BASELINE_SAMPLES if experimental else settings.MIN_BASELINE_PRODUCTION
    if len(rows) < minimum:
        return {
            "ok": False,
            "reason": (
                f"Need ≥{minimum} running-healthy windows "
                f"({'experimental' if experimental else 'production'}). Have {len(rows)}. "
                "Backfill Gbotz history or wait for MQTT. Idle/high-vib rows are excluded."
            ),
            "n_rows": len(rows),
        }

    X = np.array([row_to_vector(r) for r in rows], dtype=np.float32)
    from sklearn.ensemble import IsolationForest

    contamination = 0.05 if len(rows) >= 40 else "auto"
    model = IsolationForest(
        n_estimators=100,
        contamination=contamination,
        random_state=42,
        n_jobs=1,
    )
    model.fit(X)
    decision = model.decision_function(X)
    path = registry_path("isolation_forest.joblib")
    import joblib
    joblib.dump(model, path)
    meta = {
        "version": f"iforest-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
        "feature_keys": FEATURE_KEYS,
        "n_rows": len(rows),
        "n_machines": len({str(r.get("machineId")) for r in rows}),
        "contamination": contamination,
        "decision_mean": round(float(np.mean(decision)), 5),
        "decision_std": round(float(np.std(decision)), 5),
        "min_rpm": settings.BASELINE_MIN_RPM,
        "max_train_rms": settings.VIB_WARNING_MAX,
        "production_grade": len(rows) >= settings.MIN_BASELINE_PRODUCTION,
        "experimental": experimental,
        "source": "telemetry_reading_running_healthy",
    }
    save_registry_json("isolation_forest.json", meta)
    logger.info("[Baseline] Isolation Forest fitted on %s running-healthy rows → %s", len(rows), path)
    return {"ok": True, "path": str(path), **meta}


def features_from_live(
    imu_acceleration: float,
    temp_motor: float,
    temp_ambient: float,
    voltage_unbalance: float,
    load_pct: float,
    humidity: float,
) -> List[float]:
    return row_to_vector({
        "imuAcceleration": imu_acceleration,
        "tempMotor": temp_motor,
        "tempAmbient": temp_ambient,
        "emVoltageImbalance": voltage_unbalance,
        "emMachineLoad": load_pct,
        "humidity": humidity,
    })
