"""API alert limits for the AI Assistant.

The Gbotz platform raises its vibration alert when IMU-Acceleration leaves the
[min, max] band configured for the machine (e.g. "vibration.overall: 0.1 - outside
0.0-0.1"). The ISO 20816 zones used by Agent Gamma can call the same reading healthy,
so the card disagreed with the platform. This module fetches the band
(POST /api/ai/query, include=["alertProfile"]) so Gamma can honour it.

Fail-open by design: no key, unknown machine, network error or no band -> None, and the
existing ISO behaviour is unchanged.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Tuple

from src.services import gbotz_client
from src.utils.logger import get_logger

logger = get_logger("Services.ApiLimits")

_TTL_OK_S = 300.0
_TTL_MISS_S = 60.0
_cache: Dict[str, Tuple[float, Optional[Tuple[float, float]]]] = {}
_lock = threading.Lock()


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_vibration_band(alert_profile: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """(min, max) of IMU-Acceleration / Acceleration from an alertProfile, or None."""
    groups = (alert_profile or {}).get("parameters") or []
    for group in groups if isinstance(groups, list) else []:
        if str(group.get("name", "")).strip().lower() != "imu-acceleration":
            continue
        for p in group.get("parameters") or []:
            if str(p.get("name", "")).strip().lower() != "acceleration" or p.get("enabled") is False:
                continue
            hi = _num(p.get("max"))
            lo = _num(p.get("min"))
            if hi is not None:
                return (lo if lo is not None else 0.0, hi)
    return None


def get_vibration_band(machine_id: str) -> Optional[Tuple[float, float]]:
    if not machine_id or not gbotz_client.api_key_configured():
        return None
    now = time.time()
    with _lock:
        hit = _cache.get(machine_id)
        if hit and now < hit[0]:
            return hit[1]
    band: Optional[Tuple[float, float]] = None
    try:
        payload = gbotz_client.query({"machineIds": [machine_id], "include": ["alertProfile"]})
        for m in payload.get("machines") or []:
            if str(m.get("machineId", "")).lower() == machine_id.lower():
                band = parse_vibration_band(m.get("alertProfile") or {})
    except Exception as exc:  # unknown machine (404), timeout, bad payload ...
        logger.debug(f"API vibration band unavailable for {machine_id}: {exc}")
    with _lock:
        _cache[machine_id] = (now + (_TTL_OK_S if band else _TTL_MISS_S), band)
    return band
