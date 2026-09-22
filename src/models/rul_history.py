"""Use prior agent_snapshot rows when RUL needs more than the current MQTT tick."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

IMU_VELOCITY_HARD_MAX_MM_S = 40.0


def plausible_rms(value: Any) -> Optional[float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    rms = float(value)
    if not math.isfinite(rms) or rms <= 0.01 or rms > IMU_VELOCITY_HARD_MAX_MM_S:
        return None
    return rms


def last_plausible_rms(history: List[Dict[str, Any]]) -> Optional[float]:
    for row in history or []:
        found = plausible_rms(row.get("imuAcceleration"))
        if found is not None:
            return found
    return None


def rms_for_rul(current: float, imu_sent: bool, history: List[Dict[str, Any]]) -> Tuple[float, str]:
    """Current RMS when the IMU is on this packet; otherwise last stored agent RMS."""
    now = plausible_rms(current) if imu_sent else None
    if now is not None:
        prev = last_plausible_rms(history)
        if prev is not None and (0.5 * now) <= prev <= (2.0 * now):
            return round(0.7 * now + 0.3 * prev, 4), "smoothed_current"
        return now, "current"
    prev = last_plausible_rms(history)
    if prev is not None:
        return prev, "held_history"
    return float(current or 0.0), "current"


def blend_rul_hours(
    current_hours: float,
    history: List[Dict[str, Any]],
    run_hours: float,
) -> Tuple[float, bool]:
    """Decay the last stored RUL by elapsed run-hours and blend with this tick."""
    if current_hours <= 0 or not history:
        return current_hours, False
    prev = history[0]
    prev_rul = prev.get("rulOperatingHours")
    if not isinstance(prev_rul, (int, float)) or float(prev_rul) <= 0:
        return current_hours, False
    prev_run = prev.get("runHours")
    elapsed = 0.0
    if isinstance(prev_run, (int, float)):
        elapsed = max(0.0, float(run_hours) - float(prev_run))
    decayed = max(24.0, float(prev_rul) - elapsed)
    blended = 0.65 * float(current_hours) + 0.35 * decayed
    return max(24.0, blended), True
