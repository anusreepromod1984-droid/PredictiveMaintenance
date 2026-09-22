"""
Weibull survival. Uses fitted β, η from data/training/registry/weibull.json when present.
"""

import math
from typing import Dict, Any, Optional, Tuple

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("Models.WeibullSurvival")


def _load_fitted(asset_class: str) -> Tuple[float, float, bool, str]:
    try:
        from src.mlops.store import load_registry_json
        artifact = load_registry_json("weibull.json") or {}
        cfg = (artifact.get("classes") or {}).get(asset_class) or (artifact.get("classes") or {}).get(settings.DEFAULT_BEARING_TYPE)
        if cfg:
            return float(cfg["beta"]), float(cfg["eta"]), True, artifact.get("version", "fitted")
    except Exception as exc:
        logger.warning(f"Weibull registry not loaded: {exc}")
    return 2.5, 8500.0, False, "unfitted_default"


class WeibullSurvivalModel:
    def __init__(self, beta: float = 2.2, eta: float = 6500.0, asset_class: str = "SKF-6206"):
        # Defaults calibrated for Elson EL30 reciprocating piston compressor:
        # beta=2.2 (fatigue wear mode, rolling element under shock load from pistons)
        # eta=6500 hrs (3HP small piston compressor L10 characteristic life, less than 8500 for large rotary motors)
        # asset_class=SKF-6206 (30mm bore, crankshaft bearing on Elson EL30)
        loaded_beta, loaded_eta, fitted, version = _load_fitted(asset_class)
        self.beta = loaded_beta if fitted else beta
        self.eta = loaded_eta if fitted else eta
        self.fitted = fitted
        self.version = version
        self.asset_class = asset_class
        if fitted:
            logger.info(f"Loaded fitted Weibull {asset_class}: beta={self.beta} eta={self.eta} ({version})")
        else:
            logger.info("Weibull using unfitted defaults beta=2.5 eta=8500 until life events are provided.")

    def calculate_survival_probability(self, operating_hours: float) -> float:
        if operating_hours <= 0:
            return 1.0
        return math.exp(-((operating_hours / self.eta) ** self.beta))

    def predict_weibull_rul(
        self,
        operating_hours: float,
        vibration_rms: float,
        asset_class: Optional[str] = None,
    ) -> Dict[str, Any]:
        cls = asset_class or self.asset_class
        self.beta, self.eta, self.fitted, self.version = _load_fitted(cls)
        self.asset_class = cls

        current_survival_prob = self.calculate_survival_probability(operating_hours)
        stress_factor = (vibration_rms / 2.0) ** 1.8 if vibration_rms > 2.0 else 1.0
        effective_eta = self.eta / max(1.0, stress_factor)
        b10_life_hours = effective_eta * ((-math.log(0.10)) ** (1.0 / self.beta))
        remaining_hours = max(12.0, b10_life_hours - operating_hours)
        if vibration_rms > 7.0:
            remaining_hours = min(remaining_hours, 276.0)

        return {
            "survival_probability_pct": round(current_survival_prob * 100.0, 2),
            "weibull_rul_hours": round(remaining_hours, 1),
            "weibull_rul_days": round(remaining_hours / 24.0, 1),
            "shape_beta": self.beta,
            "scale_eta": self.eta,
            "fitted": self.fitted,
            "version": self.version,
        }
