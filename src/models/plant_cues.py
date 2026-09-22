"""Plant-level condition cues from live MQTT + agent_snapshot history.

Standards used (evaluation, not marketing labels):
- ISO 20816-3 — vibration zone from the worst orthogonal axis
- ISO 11011 / ISO 1217 — compressed-air leak as a process defect
- ISO 22096 — airborne acoustic as complementary evidence
- IEEE 519-2022 — voltage THD at the PCC
- IEC 61000-4-30 / NEMA MG-1 — voltage unbalance
- IEC 60034-1 / IEC 60038 — frequency and 400 V band
- IEC 60034-1 Class F — stator temperature

Refill Recovery Rule (§ Compressed-Air Leak Logic):
  PF001 is cleared immediately once the tank pressure is confirmed RISING and
  has crossed PRESSURE_REFILL_CLEAR_BAR (default 6.5 bar), rather than
  waiting for the gateway to retract its fault code at > 7.0 bar.
  This gives operators an accurate, lag-free Normal state on the dashboard
  while the compressor is still re-pressurising the tank.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.config import settings
from src.models.live_cues import is_operating, mic_peaks
from src.schemas.telemetry import HarmonicPeak, TelemetryFrame
from src.utils.logger import get_logger

logger = get_logger("Models.PlantCues")

# ── Refill Recovery threshold (bar) ─────────────────────────────────────────
# When the compressor auto-starts after a low-pressure event, the tank pressure
# rises from ~6.0 bar back toward the cut-out point (~7.5 bar).  The gateway
# firmware only clears its PF001 flag above 7.0 bar.  We clear it earlier so
# the dashboard reflects reality:
#   Pressure climbing through 6.5 bar  →  fault suppressed  →  status = Normal
# Change this constant if the plant's cut-in setpoint is adjusted.
PRESSURE_REFILL_CLEAR_BAR: float = 6.5
# Minimum number of consecutive history readings that must be LOWER than the
# current value to confirm that pressure is genuinely rising (not just noise).
_REFILL_RISING_CONFIRM: int = 2


def active_gateway_faults(frame: TelemetryFrame) -> List[Dict[str, Any]]:
    active: List[Dict[str, Any]] = []
    for item in frame.motor_faults or []:
        if not isinstance(item, dict):
            continue
        confidence = float(item.get("confidence") or 0.0)
        if item.get("active") or confidence >= 0.5:
            active.append(item)
    return active


def gateway_leak_fault(frame: TelemetryFrame) -> Optional[Dict[str, Any]]:
    for item in active_gateway_faults(frame):
        code = str(item.get("fault_code") or "").upper()
        desc = str(item.get("description") or "").lower()
        if code.startswith("PF") or "leak" in desc or "pressure" in desc:
            return item
    return None


def acoustic_airborne_leak(frame: TelemetryFrame) -> Optional[HarmonicPeak]:
    """ISO 22096 complementary: mid/high-frequency airborne energy while the screw is on."""
    if not is_operating(frame):
        return None
    if not (frame.field_was_sent("soundLevel") and float(frame.sound_level) >= 0.0):
        return None
    peaks = [p for p in mic_peaks(frame) if 400.0 <= float(p.frequency) <= 8000.0]
    if not peaks:
        return None
    loudest = max(peaks, key=lambda peak: float(peak.amplitude))
    if float(loudest.amplitude) < -8.0:
        return None
    return loudest


def pressure_decay_bar(history: List[Dict[str, Any]], current: Optional[float]) -> Optional[float]:
    """ISO 11011 leak survey: bar dropped vs recent running baseline.

    Returns the decay magnitude (bar) when the compressor is running and pressure
    has fallen at least 0.35 bar below the short-term average, or None otherwise.
    """
    if current is None:
        return None
    recent: List[float] = []
    for row in (history or [])[:12]:
        raw = row.get("pressure")
        if isinstance(raw, (int, float)) and float(raw) > 0.1:
            recent.append(float(raw))
    if len(recent) < 3:
        return None
    curr_val = float(current)
    if curr_val >= recent[0] - 0.05:
        return None
    baseline = sum(recent[:6]) / len(recent[:6])
    drop = baseline - curr_val
    return drop if drop >= 0.35 else None


def pressure_is_recovering(
    history: List[Dict[str, Any]],
    current: Optional[float],
    *,
    threshold_bar: float = PRESSURE_REFILL_CLEAR_BAR,
    confirm_count: int = _REFILL_RISING_CONFIRM,
) -> bool:
    """Return True when the tank is confirmed RISING and has crossed threshold_bar.

    Decision logic (all three conditions must be met):
      1. current pressure >= threshold_bar (default 6.5 bar)
      2. At least `confirm_count` of the most-recent history readings are
         strictly lower than `current`  →  proves an upward trend, not noise
      3. At least one history reading is available (can't confirm rising with
         zero history — conservative: return False when uncertain)

    This is the single gating function for the Refill Recovery Rule.
    Only call this when PF001 is active; do not call on healthy frames.
    """
    if current is None:
        return False
    curr_val = float(current)
    if curr_val < threshold_bar:
        return False

    # Build a compact list of valid recent pressure readings.
    recent: List[float] = []
    for row in (history or [])[:8]:
        raw = row.get("pressure")
        if isinstance(raw, (int, float)) and float(raw) > 0.1:
            recent.append(float(raw))

    if not recent:
        # No history → cannot confirm rising trend → stay conservative.
        return False

    # Count how many recent readings are below current (rising confirmation).
    lower_count = sum(1 for p in recent if p < curr_val - 0.02)
    confirmed_rising = lower_count >= confirm_count

    if confirmed_rising:
        logger.debug(
            "[Refill Recovery] Pressure %.2f bar >= %.2f bar threshold, "
            "%d/%d history readings below current — recovery confirmed.",
            curr_val, threshold_bar, lower_count, len(recent),
        )
    return confirmed_rising


def effective_load_pct(frame: TelemetryFrame) -> float:
    """Nameplate % from kW / rated kW. Meter %load is often CT full-scale (4 A / 5 A = 80%)."""
    rated = max(float(settings.MOTOR_RATED_KW), 1.0)
    reported = float(frame.em_power or 0.0)
    estimated = getattr(frame, "em_power_estimated", None)
    kw = reported if reported > 0.05 else (float(estimated) if isinstance(estimated, (int, float)) else 0.0)
    nameplate = min(120.0, kw / rated * 100.0) if kw > 0.05 else 0.0
    meter = float(frame.em_machine_load) if frame.field_was_sent("emMachineLoad") else 0.0
    i_max = max(float(frame.em_ir or 0.0), float(frame.em_iy or 0.0), float(frame.em_ib or 0.0))
    electrically_on = reported > 0.2 or i_max > 0.5
    if nameplate > 0.0 and abs(meter - nameplate) > 20.0:
        return round(nameplate, 1)
    if electrically_on and meter > 2.0:
        return meter
    return nameplate


def process_leak_overlay(
    frame: TelemetryFrame,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """ISO 11011 leak when the gateway flags PF00x, pressure is decaying, or airborne energy agrees.

    Refill Recovery Rule:
      If the gateway is still asserting PF001 but the current pressure has risen
      back to >= PRESSURE_REFILL_CLEAR_BAR AND is confirmed climbing (trend
      check against recent history), this function returns None immediately —
      effectively clearing the fault on the dashboard without waiting for the
      gateway firmware to retract its own flag at > 7.0 bar.
    """
    gateway = gateway_leak_fault(frame)

    # ── Refill Recovery fast-path ────────────────────────────────────────────
    # The gateway keeps PF001 asserted until pressure > 7.0 bar.  If the tank
    # is already at or above PRESSURE_REFILL_CLEAR_BAR and the trend is rising,
    # suppress PF001 immediately so the dashboard shows Normal during refill.
    if gateway and frame.field_was_sent("pressure"):
        curr_p: Optional[float] = None
        try:
            curr_p = float(frame.pressure)
        except (TypeError, ValueError):
            pass
        if pressure_is_recovering(history or [], curr_p):
            logger.info(
                "[PF001 Refill Recovery] Pressure %.2f bar rising through %.1f bar threshold — "
                "suppressing gateway PF001 (gateway will self-clear above 7.0 bar).",
                curr_p or 0.0,
                PRESSURE_REFILL_CLEAR_BAR,
            )
            return None
    # ────────────────────────────────────────────────────────────────────────

    drop = None
    if frame.field_was_sent("pressure") and is_operating(frame):
        drop = pressure_decay_bar(history or [], float(frame.pressure))
    acoustic = acoustic_airborne_leak(frame)
    if not gateway and drop is None and acoustic is None:
        return None
    if not gateway and drop is None:
        curr_p = float(frame.pressure or 0.0) if frame.field_was_sent("pressure") else None
        if curr_p is not None and history:
            # If historian shows compressor is holding operating pressure (>= 5.5 bar)
            # or pressure is building up / stable, audible mechanical sound is running noise.
            if curr_p >= 5.5:
                return None
            recent_snaps = history[:8]
            recent_pressures = [
                float(r.get("pressure", 0)) for r in recent_snaps
                if isinstance(r.get("pressure"), (int, float)) and float(r.get("pressure", 0)) > 0
            ]
            if len(recent_pressures) >= 2 and (
                curr_p >= min(recent_pressures) - 0.05
                or max(recent_pressures) - min(recent_pressures) < 0.25
            ):
                return None
        elif curr_p is not None and curr_p >= 7.0:
            return None
    if gateway:
        confidence = min(95.0, max(70.0, float(gateway.get("confidence") or 0.9) * 100.0 if float(gateway.get("confidence") or 0) <= 1.0 else float(gateway.get("confidence") or 90.0)))
        description = str(gateway.get("description") or "Compressed-air leakage")
        method = "iso11011_gateway_pf"
        hours = 48.0
    elif drop is not None:
        confidence = 74.0
        description = f"Discharge pressure falling {drop:.2f} bar vs recent running baseline (ISO 11011 leak survey)"
        method = "iso11011_pressure_decay"
        hours = 72.0
    else:
        peak = acoustic
        confidence = 66.0
        description = (
            f"Airborne leak signature {float(peak.frequency):.0f} Hz "
            f"(ISO 22096 complementary to vibration RMS)"
        )
        method = "iso22096_airborne"
        hours = 168.0
    return {
        "defect_code": "PF001",
        "defect_name": description,
        "failing_component": "Compressed-air circuit / fittings / drain traps",
        "confidence_percentage": round(confidence, 1),
        "diagnosis_method": method,
        "inspect_hours": hours,
    }
