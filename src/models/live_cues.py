"""Cues from the live pdm/integrated_data packet (Product A/B/C)."""

import math
from typing import List, Optional

from src.schemas.telemetry import HarmonicPeak, TelemetryFrame


def max_thd_pct(frame: TelemetryFrame) -> float:
    return max(
        float(frame.em_thd or 0.0),
        float(frame.em_thd_v or 0.0),
        float(frame.em_thd_vy or 0.0),
        float(frame.em_thd_vb or 0.0),
    )


def grid_hz(frame: TelemetryFrame) -> float:
    freq = float(frame.em_frequency or 0.0)
    return freq if 40.0 <= freq <= 65.0 else 50.0


def is_electrically_loaded(frame: TelemetryFrame) -> bool:
    """CTs or kW only. Meter %load is often CT full-scale, not 37 kW nameplate."""
    i_max = max(float(frame.em_ir), float(frame.em_iy), float(frame.em_ib))
    return i_max > 0.5 or float(frame.em_power) > 0.2


def is_process_running(frame: TelemetryFrame) -> bool:
    """Screw is on even when CTs still publish 0 A / 0 kW (pressure + heat + noise)."""
    pressure_up = frame.field_was_sent("pressure") and float(frame.pressure) >= 5.0
    hot = frame.field_was_sent("tempMotor") and float(frame.temp_motor) >= 38.0
    loud = frame.field_was_sent("soundLevel") and float(frame.sound_level) >= 0.0
    return (pressure_up and hot) or (hot and loud)


def is_operating(frame: TelemetryFrame) -> bool:
    """True once the compressor is electrically loaded or process-running."""
    return is_electrically_loaded(frame) or is_process_running(frame)


def three_phase_kw(frame: TelemetryFrame) -> float:
    v_avg = (float(frame.em_vr) + float(frame.em_vy) + float(frame.em_vb)) / 3.0
    i_avg = (float(frame.em_ir) + float(frame.em_iy) + float(frame.em_ib)) / 3.0
    pf = float(frame.em_power_factor) if frame.em_power_factor is not None else 0.9
    if v_avg <= 0.0 or i_avg <= 0.0:
        return 0.0
    return (math.sqrt(3.0) * v_avg * i_avg * max(0.0, min(pf, 1.0))) / 1000.0


def package_temp_c(frame: TelemetryFrame) -> float:
    if frame.field_was_sent("tempCompressor"):
        return float(frame.temp_compressor)
    if frame.field_was_sent("tempAmbient"):
        return float(frame.temp_ambient)
    return float(frame.temp_ambient or 0.0)


def ntc_delta_c(frame: TelemetryFrame) -> float:
    return float(frame.temp_motor) - package_temp_c(frame)


def mic_peaks(frame: TelemetryFrame) -> List[HarmonicPeak]:
    return list(frame.mic_harmonics or [])


def acoustic_two_x(frame: TelemetryFrame) -> Optional[HarmonicPeak]:
    """Nearest mic peak to 2× line frequency (2-pole 2X ≈ 100 Hz at 50 Hz grid)."""
    peaks = mic_peaks(frame)
    if not peaks:
        return None
    target = 2.0 * grid_hz(frame)
    best = min(peaks, key=lambda peak: abs(float(peak.frequency) - target))
    if abs(float(best.frequency) - target) > 15.0:
        return None
    return best


def dominant_mic_hz(frame: TelemetryFrame) -> List[float]:
    return [round(float(peak.frequency), 1) for peak in mic_peaks(frame)]
