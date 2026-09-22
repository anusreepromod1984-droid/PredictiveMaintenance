"""Train XGBoost only when labeled windows meet minima. Rules remain the default otherwise."""

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from src.config import settings
from src.mlops.store import load_labeled_windows, registry_path, save_registry_json
from src.schemas.training import LabeledWindow

FEATURE_KEYS = [
    "imuAcceleration",
    "emVoltageImbalance",
    "emMachineLoad",
    "tempMotor",
    "tempAmbient",
    "deltaTemp",
    "patternCode",
    "domainCode",
    "matchedCode",
]

PATTERN_CODES = {
    "STABLE": 0, "STEADY_CLIMB": 1, "STUCK_HIGH": 2, "SUDDEN_JUMP_FLAT": 3,
    "NOISE_EXPANSION": 4, "REPEATED_SPIKES": 5, "HARDWARE_DISCONNECT": 6,
}
DOMAIN_CODES = {"MECHANICAL": 0, "ELECTRICAL": 1, "MIXED": 2, "ENVIRONMENTAL": 3}
MATCH_CODES = {
    "NONE": 0, "1X_RPM": 1, "2X_RPM": 2, "BPFI": 3, "BPFO": 4,
    "MF001_MECHANICAL_LOOSENESS": 5, "MF002_SHAFT_MISALIGNMENT": 6, "MF003_ROTOR_IMBALANCE": 7,
}


def window_to_vector(window: LabeledWindow) -> List[float]:
    f = window.features or {}
    rms = float(f.get("imuAcceleration") or f.get("imu_acceleration") or 0)
    vuf = float(f.get("emVoltageImbalance") or f.get("em_voltage_imbalance") or 0)
    load = float(f.get("emMachineLoad") or f.get("em_machine_load") or 0)
    t_m = float(f.get("tempMotor") or f.get("temp_motor") or 0)
    t_a = float(f.get("tempAmbient") or f.get("temp_ambient") or 25)
    pattern = str(f.get("pattern") or f.get("pattern_recognition_status") or "STABLE")
    domain = str(f.get("isolatedDomain") or f.get("isolated_failure_domain") or "MECHANICAL")
    matched = str(f.get("matchedFault") or f.get("matched_signal_fault") or "NONE")
    return [
        rms, vuf, load, t_m, t_a, t_m - t_a,
        float(PATTERN_CODES.get(pattern, 0)),
        float(DOMAIN_CODES.get(domain, 0)),
        float(MATCH_CODES.get(matched, 0)),
    ]


def features_from_live(
    imu_acceleration: float,
    voltage_unbalance: float,
    load_pct: float,
    temp_motor: float,
    temp_ambient: float,
    pattern: str,
    isolated_domain: str,
    matched_signal_fault: str,
) -> List[float]:
    fake = LabeledWindow(
        machineId="_",
        timestamp="now",
        label="NORMAL",
        features={
            "imuAcceleration": imu_acceleration,
            "emVoltageImbalance": voltage_unbalance,
            "emMachineLoad": load_pct,
            "tempMotor": temp_motor,
            "tempAmbient": temp_ambient,
            "pattern": pattern,
            "isolatedDomain": isolated_domain,
            "matchedFault": matched_signal_fault,
        },
    )
    return window_to_vector(fake)


def fit_classifier(experimental: bool = False) -> Dict[str, Any]:
    windows = [w for w in load_labeled_windows() if w.label != "SENSOR"]
    by_label = Counter(w.label for w in windows)
    minimum = settings.MIN_CLASSIFIER_PER_CLASS if experimental else settings.MIN_CLASSIFIER_PRODUCTION
    if len(by_label) < 2:
        return {"ok": False, "reason": "Need at least 2 classes (including NORMAL).", "by_label": dict(by_label)}
    short = {k: v for k, v in by_label.items() if v < minimum}
    if short:
        return {
            "ok": False,
            "reason": (
                f"Need ≥{minimum} labeled windows per class "
                f"({'experimental' if experimental else 'production'}). Short: {short}"
            ),
            "by_label": dict(by_label),
        }

    X = np.array([window_to_vector(w) for w in windows], dtype=np.float32)
    labels = [w.label for w in windows]
    class_names = sorted(set(labels))
    y = np.array([class_names.index(l) for l in labels], dtype=np.int32)

    import xgboost as xgb

    params = dict(
        n_estimators=80,
        max_depth=4,
        learning_rate=0.1,
        subsample=0.9,
        colsample_bytree=0.9,
        n_jobs=1,
    )
    if len(class_names) == 2:
        params.update(objective="binary:logistic", eval_metric="logloss")
    else:
        params.update(objective="multi:softprob", eval_metric="mlogloss")
    clf = xgb.XGBClassifier(**params)
    clf.fit(X, y)
    model_path = registry_path("classifier.ubj")
    clf.save_model(str(model_path))
    meta = {
        "version": f"xgb-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
        "class_names": class_names,
        "feature_keys": FEATURE_KEYS,
        "n_windows": len(windows),
        "by_label": dict(by_label),
        "production_grade": all(v >= settings.MIN_CLASSIFIER_PRODUCTION for v in by_label.values()),
        "experimental": experimental,
    }
    save_registry_json("classifier.json", meta)
    return {"ok": True, "path": str(model_path), **meta}
