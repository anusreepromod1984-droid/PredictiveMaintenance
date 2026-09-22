"""
ISO 13379 catalog/rule classifier.
Loads data/training/registry/classifier.ubj when a fit has been run; otherwise rules only.
"""

from typing import Dict, Any, Optional
from src.config import settings
from src.models.iso_20816 import classify_velocity_zone
from src.utils.logger import get_logger

logger = get_logger("Models.FaultClassifier")

_DEFECT_NAMES = {
    "NORMAL": "Normal Operation",
    "BPFI": "Bearing Inner Race Fatigue Pitting",
    "BPFO": "Bearing Outer Race Flaking",
    "MF001": "Mechanical Structural Looseness",
    "MF002": "Shaft Angular & Parallel Misalignment",
    "MF003": "Rotor Dynamic Imbalance",
    "EF001": "3-Phase Voltage Unbalance & Stator Stress",
    "ANOMALY_UNCLASSIFIED": "Elevated Mechanical Vibration (Developing Wear)",
    "SENSOR": "Instrumentation / Sensor Fault",
}
_DEFECT_COMPONENTS = {
    "NORMAL": "All Components Operating Within Baseline",
    "BPFI": "Drive-End SKF-6208 Bearing Inner Race",
    "BPFO": "Non-Drive-End Bearing Outer Race",
    "MF001": "Compressor Mounting Base Bolts",
    "MF002": "Motor-to-Compressor Coupling & Shimming",
    "MF003": "Compressor Rotor / Drive Assembly",
    "EF001": "Electric Motor Stator Winding / Power Supply",
    "ANOMALY_UNCLASSIFIED": "Mechanical Drive Assembly / Bearings",
    "SENSOR": "Vibration / temperature transducer",
}


class FaultClassifierEngine:
    """Rule + catalog match. XGBoost only when data/training/registry/classifier.ubj exists."""

    def __init__(self):
        self.model = None
        self.class_names = []
        self.version = "iso13379_rules"
        self._logged_missing = False
        self._try_load()

    def _try_load(self) -> None:
        try:
            from src.mlops.store import load_registry_json, registry_path
            meta = load_registry_json("classifier.json")
            path = registry_path("classifier.ubj")
            if meta and path.exists():
                import xgboost as xgb
                clf = xgb.XGBClassifier()
                clf.load_model(str(path))
                self.model = clf
                self.class_names = meta.get("class_names") or []
                self.version = meta.get("version", "xgb")
                logger.info(f"Loaded trained classifier {self.version} classes={self.class_names}")
            elif not self._logged_missing:
                logger.info("No classifier artifact — using ISO 13379 rules.")
                self._logged_missing = True
        except Exception as exc:
            logger.warning(f"Classifier artifact not loaded: {exc}")

    def classify_defect(
        self,
        vibration_rms: float,
        matched_signal_fault: str,
        voltage_unbalance: float,
        load_pct: float,
        temp_motor: float,
        temp_ambient: float,
        pattern: str = "STABLE",
        isolated_domain: str = "MECHANICAL",
    ) -> Dict[str, Any]:
        if self.model is None:
            self._try_load()
        if self.model is not None and self.class_names:
            from src.mlops.fit_classifier import features_from_live
            vec = features_from_live(
                vibration_rms, voltage_unbalance, load_pct, temp_motor, temp_ambient,
                pattern, isolated_domain, matched_signal_fault,
            )
            import numpy as np
            proba = self.model.predict_proba(np.array([vec], dtype=np.float32))[0]
            idx = int(proba.argmax())
            code = self.class_names[idx]
            return {
                "defect_code": code,
                "defect_name": _DEFECT_NAMES.get(code, code),
                "failing_component": _DEFECT_COMPONENTS.get(code, "Unknown"),
                "confidence_percentage": round(float(proba[idx]) * 100.0, 1),
                "diagnosis_method": self.version,
            }
        return self._rules(
            vibration_rms, matched_signal_fault, voltage_unbalance, load_pct,
            temp_motor, temp_ambient, pattern, isolated_domain,
        )

    def classify_from_rms(
        self,
        vibration_rms: float,
        voltage_unbalance: float,
        temp_motor: float,
        temp_ambient: float,
        isolated_domain: str = "MECHANICAL",
        group: Optional[str] = None,
        support: Optional[str] = None,
    ) -> Dict[str, Any]:
        """ISO 20816 severity from broadband RMS only. Does not name BPFI/MF00x."""
        electrical_first = isolated_domain in {"ELECTRICAL", "MIXED"} or voltage_unbalance > settings.VUF_DANGER_MIN
        if electrical_first and voltage_unbalance > settings.VUF_NORMAL_MAX:
            return {
                "defect_code": "EF001",
                "defect_name": "3-Phase Voltage Unbalance & Stator Stress",
                "failing_component": "Electric Motor Stator Winding / Power Supply",
                "confidence_percentage": 86.0,
                "diagnosis_method": "iso20816_rms_only",
            }

        zone = classify_velocity_zone(vibration_rms, group, support)
        if zone in {"A", "B"}:
            return {
                "defect_code": "NORMAL",
                "defect_name": f"Normal Operation (ISO 20816 Zone {zone}, RMS {vibration_rms:.3f} mm/s)",
                "failing_component": "All Components Operating Within Baseline",
                "confidence_percentage": 90.0 if zone == "A" else 82.0,
                "diagnosis_method": "iso20816_rms_only",
            }
        if zone == "C":
            return {
                "defect_code": "ANOMALY_UNCLASSIFIED",
                "defect_name": f"ISO 20816 Zone C — elevated RMS {vibration_rms:.2f} mm/s (no spectrum to localize)",
                "failing_component": "Mechanical Drive Assembly / Bearings",
                "confidence_percentage": 72.0,
                "diagnosis_method": "iso20816_rms_only",
            }
        return {
            "defect_code": "ANOMALY_UNCLASSIFIED",
            "defect_name": f"ISO 20816 Zone D — unacceptable RMS {vibration_rms:.2f} mm/s (no spectrum to localize)",
            "failing_component": "Mechanical Drive Assembly / Bearings",
            "confidence_percentage": 80.0,
            "diagnosis_method": "iso20816_rms_only",
        }

    def _rules(
        self,
        vibration_rms: float,
        matched_signal_fault: str,
        voltage_unbalance: float,
        load_pct: float,
        temp_motor: float,
        temp_ambient: float,
        pattern: str,
        isolated_domain: str,
    ) -> Dict[str, Any]:
        electrical_first = isolated_domain in {"ELECTRICAL", "MIXED"} or voltage_unbalance > settings.VUF_DANGER_MIN

        if (
            vibration_rms < settings.VIB_NORMAL_MAX
            and voltage_unbalance < settings.VUF_NORMAL_MAX
            and (temp_motor - temp_ambient) < settings.GENUINE_OVERHEAT_DELTA_C
            and pattern in {"STABLE", "HARDWARE_DISCONNECT"}
            and isolated_domain != "ELECTRICAL"
        ):
            return {
                "defect_code": "NORMAL",
                "defect_name": "Normal Operation",
                "failing_component": "All Components Operating Within Baseline",
                "confidence_percentage": 88.0,
                "diagnosis_method": "iso13379_catalog_rules",
            }

        if electrical_first and voltage_unbalance > settings.VUF_NORMAL_MAX:
            return {
                "defect_code": "EF001",
                "defect_name": "3-Phase Voltage Unbalance & Stator Stress",
                "failing_component": "Electric Motor Stator Winding / Power Supply",
                "confidence_percentage": 86.0,
                "diagnosis_method": "iso13379_catalog_rules",
            }

        if matched_signal_fault in {"BPFI", "BPFI_BEARING_INNER_RACE_PITTING"}:
            return {
                "defect_code": "BPFI",
                "defect_name": "Bearing Inner Race Fatigue Pitting",
                "failing_component": "Drive-End SKF-6208 Bearing Inner Race",
                "confidence_percentage": 84.0,
                "diagnosis_method": "iso13379_catalog_rules",
            }

        if matched_signal_fault in {"BPFO", "BPFO_BEARING_OUTER_RACE_CRACK"}:
            return {
                "defect_code": "BPFO",
                "defect_name": "Bearing Outer Race Flaking",
                "failing_component": "Non-Drive-End Bearing Outer Race",
                "confidence_percentage": 83.0,
                "diagnosis_method": "iso13379_catalog_rules",
            }

        if matched_signal_fault in {"1X_RPM", "MF003_ROTOR_IMBALANCE"} or (
            vibration_rms > 1.5 and matched_signal_fault == "MF003_ROTOR_IMBALANCE"
        ):
            return {
                "defect_code": "MF003",
                "defect_name": "Rotor Dynamic Imbalance",
                "failing_component": "Compressor Rotor / Drive Assembly",
                "confidence_percentage": 82.0,
                "diagnosis_method": "iso13379_catalog_rules",
            }

        if matched_signal_fault in {"2X_RPM", "MF002_SHAFT_MISALIGNMENT"}:
            return {
                "defect_code": "MF002",
                "defect_name": "Shaft Angular & Parallel Misalignment",
                "failing_component": "Motor-to-Compressor Coupling & Shimming",
                "confidence_percentage": 85.0,
                "diagnosis_method": "iso13379_catalog_rules",
            }

        if matched_signal_fault in {"BROADBAND_IMPULSE", "MF001_MECHANICAL_LOOSENESS", "MF001", "0.5X_SUBHARMONIC"}:
            return {
                "defect_code": "MF001",
                "defect_name": "Mechanical Structural Looseness",
                "failing_component": "Compressor Mounting Base Bolts",
                "confidence_percentage": 82.0,
                "diagnosis_method": "iso13379_catalog_rules",
            }

        if pattern in {"NOISE_EXPANSION", "REPEATED_SPIKES"} and vibration_rms >= settings.VIB_WARNING_MAX:
            return {
                "defect_code": "MF001",
                "defect_name": "Mechanical Structural Looseness",
                "failing_component": "Compressor Mounting Base Bolts",
                "confidence_percentage": 78.0,
                "diagnosis_method": "iso13379_catalog_rules",
            }

        if pattern in {"STEADY_CLIMB", "STUCK_HIGH"} and vibration_rms >= settings.VIB_NORMAL_MAX:
            return {
                "defect_code": "BPFI",
                "defect_name": "Progressive Bearing Wear (pattern-led, confirm with spectra)",
                "failing_component": "Drive-End SKF-6208 Bearing Inner Race",
                "confidence_percentage": 70.0,
                "diagnosis_method": "iso13379_catalog_rules",
            }

        if vibration_rms >= settings.VIB_WARNING_MAX:
            if matched_signal_fault in {"1X_RPM", "MF003_ROTOR_IMBALANCE"}:
                return {
                    "defect_code": "MF003",
                    "defect_name": "Rotor Dynamic Imbalance (Warning Threshold Exceeded)",
                    "failing_component": "Compressor Rotor / Drive Assembly",
                    "confidence_percentage": 88.0,
                    "diagnosis_method": "sensitive_threshold_rules",
                }
            return {
                "defect_code": "ANOMALY_UNCLASSIFIED",
                "defect_name": "Elevated Mechanical Vibration (Developing Wear)",
                "failing_component": "Mechanical Drive Assembly / Bearings",
                "confidence_percentage": 75.0,
                "diagnosis_method": "iso13379_catalog_rules",
            }

        return {
            "defect_code": "NORMAL",
            "defect_name": "Normal Operation",
            "failing_component": "All Components Operating Within Baseline",
            "confidence_percentage": 80.0,
            "diagnosis_method": "iso13379_catalog_rules",
        }
