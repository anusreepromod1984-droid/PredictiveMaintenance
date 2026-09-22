"""
Isolation Forest baseline. Loads data/training/registry/isolation_forest.joblib when fitted.
Falls back to a weighted heuristic so Beta still scores on Day-1.
ISO 20816 / NAMUR remain the alarms — this is a second opinion only.
"""

from typing import Any, Dict

from src.utils.logger import get_logger

logger = get_logger("Models.AnomalyDetector")


def _heuristic_score(
    vibration_rms: float,
    temp_motor: float,
    temp_ambient: float,
    voltage_unbalance: float,
    humidity: float,
) -> Dict[str, Any]:
    vib_score = min(1.0, vibration_rms / 7.1)
    thermal_rise = max(0.0, temp_motor - temp_ambient)
    thermal_score = min(1.0, thermal_rise / 40.0)
    elec_score = min(1.0, voltage_unbalance / 5.0)
    multivariate_score = (vib_score * 0.5 + thermal_score * 0.3 + elec_score * 0.2) * 100.0
    is_anomalous = multivariate_score > 40.0
    return {
        "anomaly_score": round(multivariate_score, 1),
        "is_anomalous": is_anomalous,
        "isolation_forest_status": "ANOMALY_DETECTED" if is_anomalous else "BASELINE_HEALTHY",
        "isolation_forest_fitted": False,
        "method": "weighted_heuristic",
    }


class AnomalyDetectorEngine:
    """Isolation Forest when an artifact exists; weighted heuristic otherwise."""

    def __init__(self):
        self.model = None
        self.version = "weighted_heuristic"
        self._try_load()

    def _try_load(self) -> None:
        try:
            from src.mlops.store import load_registry_json, registry_path
            import joblib
            meta = load_registry_json("isolation_forest.json")
            path = registry_path("isolation_forest.joblib")
            if not meta or not path.exists():
                return
            self.model = joblib.load(path)
            self.version = meta.get("version", "iforest")
            logger.info("Loaded Isolation Forest %s (%s rows)", self.version, meta.get("n_rows"))
        except Exception as exc:
            logger.warning("Isolation Forest artifact not loaded: %s", exc)
            self.model = None
            self.version = "weighted_heuristic"

    def compute_anomaly_score(
        self,
        vibration_rms: float,
        temp_motor: float,
        temp_ambient: float,
        voltage_unbalance: float,
        humidity: float,
        load_pct: float = 70.0,
    ) -> Dict[str, Any]:
        if self.model is None:
            self._try_load()
        if self.model is None:
            return _heuristic_score(
                vibration_rms, temp_motor, temp_ambient, voltage_unbalance, humidity
            )
        from src.mlops.fit_isolation_forest import features_from_live
        vector = [
            features_from_live(
                vibration_rms, temp_motor, temp_ambient, voltage_unbalance, load_pct, humidity
            )
        ]
        pred = int(self.model.predict(vector)[0])
        decision = float(self.model.decision_function(vector)[0])
        # decision_function: higher = more inlier. Map to 0–100 anomaly.
        anomaly_score = round(max(0.0, min(100.0, 50.0 - decision * 80.0)), 1)
        is_anomalous = pred == -1
        return {
            "anomaly_score": anomaly_score,
            "is_anomalous": is_anomalous,
            "isolation_forest_status": "ANOMALY_DETECTED" if is_anomalous else "BASELINE_HEALTHY",
            "isolation_forest_fitted": True,
            "method": self.version,
            "decision_function": round(decision, 5),
        }
