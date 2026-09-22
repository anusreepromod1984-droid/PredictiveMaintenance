"""ISO 20816-3 (supersedes ISO 10816-3) vibration zone evaluation.

Uses broadband velocity RMS in mm/s. Group 2 covers 15–300 kW motors (this plant's ~37 kW class).
Zone boundaries match ISO 10816-3 Table A.1, which 20816-3 retained for evaluation.
"""

from typing import List, Optional, Tuple

from src.config import settings

# (zone A/B, B/C, C/D) in mm/s RMS
_ZONE_LIMITS = {
    ("1", "rigid"): (2.3, 4.5, 7.1),
    ("1", "flexible"): (3.5, 7.1, 11.0),
    ("2", "rigid"): (1.4, 2.8, 4.5),
    ("2", "flexible"): (2.3, 4.5, 7.1),
}


def normalize_group(group: Optional[str]) -> str:
    value = str(group or settings.ISO_MACHINE_GROUP).strip().lower()
    if value in {"1", "group1", "group_1", "i"}:
        return "1"
    return "2"


def normalize_support(support: Optional[str]) -> str:
    value = str(support or settings.ISO_SUPPORT).strip().lower()
    if value in {"rigid", "stiff", "solid"}:
        return "rigid"
    return "flexible"


def zone_limits(group: Optional[str] = None, support: Optional[str] = None) -> Tuple[float, float, float]:
    key = (normalize_group(group), normalize_support(support))
    return _ZONE_LIMITS[key]


def axis_velocity_rms(frame) -> List[float]:
    """Orthogonal velocity RMS values that were actually published (mm/s)."""
    axes: List[float] = []
    pairs = (
        (("xAxisVibration", "_axisX", "x_axis_vibration"), getattr(frame, "x_axis_vibration", None)),
        (("yAxisVibration", "_axisY", "y_axis_vibration"), getattr(frame, "y_axis_vibration", None)),
        (("zAxisVibration", "_axisZ", "z_axis_vibration"), getattr(frame, "z_axis_vibration", None)),
    )
    for names, raw in pairs:
        if not frame.field_was_sent(*names):
            continue
        if not isinstance(raw, (int, float)):
            continue
        value = float(raw)
        if 0.0 <= value <= 40.0:
            axes.append(value)
    return axes


def evaluation_velocity_rms(frame, fallback_imu: Optional[float] = None) -> float:
    """ISO 20816-3 evaluates each direction; the zone is the worst (maximum) axis.

    When only IMU-Acceleration (vector/overall RMS) is published, use that.
    If X/Y/Z dwarf the published overall RMS, they are not the same mm/s quantity
    (gateway glitch) — do not Zone-D the machine from the axes.
    """
    overall = (
        max(0.0, float(fallback_imu))
        if fallback_imu is not None
        else max(0.0, float(getattr(frame, "imu_acceleration", 0.0) or 0.0))
    )
    axes = axis_velocity_rms(frame)
    if not axes:
        return overall
    worst = max(axes)
    imu_sent = hasattr(frame, "field_was_sent") and frame.field_was_sent("imuAcceleration")
    if imu_sent and overall < 2.5 and worst >= 4.5 and worst > max(overall * 3.0, overall + 2.0):
        return overall
    return worst


def classify_velocity_zone(
    velocity_mm_s_rms: float,
    group: Optional[str] = None,
    support: Optional[str] = None,
) -> str:
    ab, bc, cd = zone_limits(group, support)
    rms = max(0.0, float(velocity_mm_s_rms))
    if rms <= ab:
        return "A"
    if rms <= bc:
        return "B"
    if rms <= cd:
        return "C"
    return "D"


def zone_health_score(
    velocity_mm_s_rms: float,
    group: Optional[str] = None,
    support: Optional[str] = None,
    electrical_penalty: float = 0.0,
) -> float:
    """Map ISO zone to 5–100. Zone A is new-machine quality; D is trip/unacceptable."""
    ab, bc, cd = zone_limits(group, support)
    rms = max(0.0, float(velocity_mm_s_rms))
    if rms <= ab:
        score = 100.0 - (rms / ab) * 12.0
    elif rms <= bc:
        score = 88.0 - ((rms - ab) / (bc - ab)) * 18.0
    elif rms <= cd:
        score = 70.0 - ((rms - bc) / (cd - bc)) * 30.0
    else:
        overrun = min(1.0, (rms - cd) / max(cd, 1.0))
        score = 40.0 - overrun * 35.0
    score -= min(25.0, max(0.0, electrical_penalty))
    return round(max(5.0, min(100.0, score)), 1)
