"""Report what the plant has supplied vs minima to fit each model."""

from collections import Counter
from typing import Any, Dict, List

from src.config import settings
from src.mlops.store import load_labeled_windows, load_life_events, load_registry_json, load_trajectories, registry_path

YOU_MUST_PROVIDE = [
    "component_life_events: past bearing/winding/coupling lives (failed | replaced_pm | still_running) with run-hours — required for Weibull",
    "labeled_windows: technician/teardown/WO confirmed class per timestamp (NORMAL, BPFI, BPFO, MF001, MF002, MF003, EF001, SENSOR) — required for classifier",
    "rul_trajectories: run-to-failure curves (hours, RMS, temp, load, rpm, true remaining hours) — required for PINN",
    "running-healthy telemetry: Gbotz history backfill or MQTT into telemetry_reading — required for Isolation Forest baseline",
    "Do not use TelemetryReading_Fault_Injected_Data.csv (6 synthetic rows) as training labels",
]


def dataset_status(asset_class: str = "SKF-6208") -> Dict[str, Any]:
    events = load_life_events(asset_class)
    failures = [e for e in events if e.observed_failure]
    censored = [e for e in events if not e.observed_failure]
    windows = load_labeled_windows()
    by_label = Counter(w.label for w in windows)
    trajs = load_trajectories(asset_class)
    long_trajs = [t for t in trajs if len(t.points) >= settings.MIN_PINN_POINTS]

    weibull_fit = load_registry_json("weibull.json")
    clf_meta = load_registry_json("classifier.json")
    pinn_meta = load_registry_json("pinn.json")
    iforest_meta = load_registry_json("isolation_forest.json")

    from src.mlops.fit_isolation_forest import count_running_rows
    n_running = count_running_rows()

    return {
        "weibull": {
            "asset_class": asset_class,
            "n_failures": len(failures),
            "n_censored": len(censored),
            "minimum_to_fit": settings.MIN_WEIBULL_FAILURES,
            "recommended_production": settings.MIN_WEIBULL_RECOMMENDED,
            "ready_to_fit": len(failures) >= settings.MIN_WEIBULL_FAILURES,
            "production_grade": len(failures) >= settings.MIN_WEIBULL_RECOMMENDED,
            "artifact_loaded": bool(weibull_fit and weibull_fit.get("classes", {}).get(asset_class)),
        },
        "classifier": {
            "n_windows": len(windows),
            "by_label": dict(by_label),
            "minimum_per_class_to_fit": settings.MIN_CLASSIFIER_PER_CLASS,
            "recommended_per_class": settings.MIN_CLASSIFIER_PRODUCTION,
            "ready_to_fit": _classifier_ready(by_label, settings.MIN_CLASSIFIER_PER_CLASS),
            "production_grade": _classifier_ready(by_label, settings.MIN_CLASSIFIER_PRODUCTION),
            "artifact_loaded": registry_path("classifier.json").exists() and registry_path("classifier.ubj").exists(),
        },
        "pinn": {
            "n_trajectories": len(trajs),
            "n_trajectories_with_enough_points": len(long_trajs),
            "minimum_trajectories": settings.MIN_PINN_TRAJECTORIES,
            "minimum_points_each": settings.MIN_PINN_POINTS,
            "ready_to_fit": len(long_trajs) >= settings.MIN_PINN_TRAJECTORIES,
            "artifact_loaded": registry_path("pinn.pt").exists(),
        },
        "baseline": {
            "n_running_rows": n_running,
            "minimum_to_fit": settings.MIN_BASELINE_SAMPLES,
            "recommended_production": settings.MIN_BASELINE_PRODUCTION,
            "min_rpm": settings.BASELINE_MIN_RPM,
            "ready_to_fit": bool(n_running is not None and n_running >= settings.MIN_BASELINE_SAMPLES),
            "production_grade": bool(n_running is not None and n_running >= settings.MIN_BASELINE_PRODUCTION),
            "artifact_loaded": registry_path("isolation_forest.joblib").exists(),
            "version": (iforest_meta or {}).get("version"),
        },
        "you_must_provide": YOU_MUST_PROVIDE,
        "registry": {
            "weibull": weibull_fit is not None,
            "classifier": clf_meta is not None,
            "pinn": pinn_meta is not None,
            "isolation_forest": iforest_meta is not None,
        },
    }


def _classifier_ready(by_label: Counter, minimum: int) -> bool:
    trainable = {k: v for k, v in by_label.items() if k != "SENSOR"}
    if len(trainable) < 2:
        return False
    return all(v >= minimum for v in trainable.values())
