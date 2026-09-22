"""Fit Weibull β, η from component life events (includes right-censoring)."""

from datetime import datetime, timezone
from typing import Any, Dict

from src.config import settings
from src.mlops.store import load_life_events, save_registry_json, load_registry_json


def fit_weibull(asset_class: str = "SKF-6208", experimental: bool = False) -> Dict[str, Any]:
    events = load_life_events(asset_class)
    failures = [e for e in events if e.observed_failure]
    if len(failures) < settings.MIN_WEIBULL_FAILURES and not experimental:
        return {
            "ok": False,
            "reason": (
                f"Need at least {settings.MIN_WEIBULL_FAILURES} failed lives for {asset_class}; "
                f"have {len(failures)}. Provide CMMS replacement history. "
                f"Pass experimental=true only for a lab sketch."
            ),
            "n_failures": len(failures),
        }

    if len(failures) < 3:
        return {"ok": False, "reason": "Need at least 3 failures even for experimental fit.", "n_failures": len(failures)}

    from lifelines import WeibullFitter

    durations = [e.duration_hours for e in events if e.duration_hours > 0]
    observed = [1 if e.observed_failure else 0 for e in events if e.duration_hours > 0]
    wf = WeibullFitter()
    wf.fit(durations, event_observed=observed)

    beta = float(wf.rho_)
    eta = float(wf.lambda_)
    artifact = {
        "version": f"weibull-{asset_class}-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
        "classes": {},
    }
    existing = load_registry_json("weibull.json") or {}
    artifact["classes"] = existing.get("classes", {})
    artifact["classes"][asset_class] = {
        "beta": round(beta, 4),
        "eta": round(eta, 1),
        "n_failures": len(failures),
        "n_censored": len(events) - len(failures),
        "production_grade": len(failures) >= settings.MIN_WEIBULL_RECOMMENDED,
        "fitted_at": datetime.now(timezone.utc).isoformat(),
    }
    path = save_registry_json("weibull.json", artifact)
    return {
        "ok": True,
        "asset_class": asset_class,
        "beta": artifact["classes"][asset_class]["beta"],
        "eta": artifact["classes"][asset_class]["eta"],
        "n_failures": len(failures),
        "production_grade": artifact["classes"][asset_class]["production_grade"],
        "path": str(path),
    }
