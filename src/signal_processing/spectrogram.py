"""
0–50 Hz STFT spectrogram for structural faults (ISO 13373 / ISO 13379).
Maps persistent 1× → MF003 (eccentric load), 2× → MF002 (misalignment),
broadband vertical stripes → MF001 (looseness / impact).
Does not replace Hilbert envelope FFT (bearings live in the kHz band).
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.signal import stft

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("SignalProcessing.Spectrogram")

FAULT_CODE_MAP = {
    "1X_RPM": "MF003_ROTOR_IMBALANCE",
    "2X_RPM": "MF002_SHAFT_MISALIGNMENT",
    "BROADBAND_IMPULSE": "MF001_MECHANICAL_LOOSENESS",
    "NONE": "MECHANICAL_NORMAL",
}


class VibrationSpectrogramEngine:
    def analyze(
        self,
        *,
        rpm: float,
        sample_rate_hz: Optional[float] = None,
        waveform: Optional[Sequence[float]] = None,
        waveform_x: Optional[Sequence[float]] = None,
        waveform_y: Optional[Sequence[float]] = None,
        waveform_z: Optional[Sequence[float]] = None,
    ) -> Optional[Dict[str, Any]]:
        axes = self._collect_axes(waveform, waveform_x, waveform_y, waveform_z)
        if not axes:
            return None
        fs = float(sample_rate_hz or settings.WAVEFORM_SAMPLE_RATE_HZ)
        if fs <= 0:
            return None

        shaft = rpm / 60.0
        per_axis = []
        for name, samples in axes:
            finding = self._analyze_axis(np.asarray(samples, dtype=np.float64), fs, shaft, name)
            if finding:
                per_axis.append(finding)

        if not per_axis:
            return None

        label, event = self._combine_axes(per_axis)
        primary = per_axis[0]
        logger.info(
            f"STFT spectrogram axes={[a for a, _ in axes]} fs={fs:.0f}Hz event={event} match={label}"
        )
        return {
            "primary_peak_hz": primary["primary_peak_hz"],
            "matched_target_hz": round(
                shaft if label == "1X_RPM" else (2.0 * shaft if label == "2X_RPM" else 0.0), 2
            ),
            "matched_fault_code": FAULT_CODE_MAP.get(label, "MF001_MECHANICAL_LOOSENESS"),
            "defect_label": label,
            "method": "stft_spectrogram",
            "spectrogram_event": event,
            "peaks": primary["peaks"],
            "axis_findings": [
                {
                    "axis": f["axis"],
                    "event": f["event"],
                    "label": f["label"],
                    "primary_peak_hz": f["primary_peak_hz"],
                }
                for f in per_axis
            ],
        }

    def _collect_axes(
        self,
        waveform: Optional[Sequence[float]],
        waveform_x: Optional[Sequence[float]],
        waveform_y: Optional[Sequence[float]],
        waveform_z: Optional[Sequence[float]],
    ) -> List[Tuple[str, Sequence[float]]]:
        z = waveform_z if waveform_z is not None else waveform
        ordered = [("X", waveform_x), ("Y", waveform_y), ("Z", z)]
        out = []
        for name, samples in ordered:
            if samples is None:
                continue
            if len(samples) < settings.SPECTROGRAM_MIN_SAMPLES:
                continue
            out.append((name, samples))
        return out

    def _analyze_axis(self, x: np.ndarray, fs: float, shaft_hz: float, axis: str) -> Optional[Dict[str, Any]]:
        if x.size > settings.ENVELOPE_MAX_SAMPLES:
            x = x[: settings.ENVELOPE_MAX_SAMPLES]
        x = x - np.mean(x)
        nperseg = int(min(settings.SPECTROGRAM_NPERSEG, max(64, x.size // 4)))
        nperseg = min(nperseg, x.size)
        if nperseg < 32:
            return None
        freqs, _, zxx = stft(x, fs=fs, window="hann", nperseg=nperseg, noverlap=nperseg // 2, boundary=None)
        mag = np.abs(zxx)
        fmax = min(settings.SPECTROGRAM_MAX_FREQ_HZ, 0.45 * fs)
        band = (freqs >= 1.0) & (freqs <= fmax)
        if not np.any(band) or mag.shape[1] < 2:
            return None
        f = freqs[band]
        m = mag[band, :]

        frame_energy = m.sum(axis=0)
        floor = float(np.median(frame_energy) + 1e-12)
        stripe_mask = frame_energy > max(floor * 5.0, float(np.max(frame_energy)) * 0.35)
        flatness = self._spectral_flatness(m)
        impulse_mask = stripe_mask & (flatness > 0.35)
        n_stripes = int(np.sum(impulse_mask))

        p1 = self._band_power(f, m, shaft_hz)
        p2 = self._band_power(f, m, 2.0 * shaft_hz)
        mean1 = float(np.mean(p1))
        mean2 = float(np.mean(p2))
        newest = max(1, m.shape[1] // 4)
        rise1 = float(np.mean(p1[-newest:]) / (np.mean(p1[:newest]) + 1e-12))
        rise2 = float(np.mean(p2[-newest:]) / (np.mean(p2[:newest]) + 1e-12))

        mean_spec = m.mean(axis=1)
        peak_idx = int(np.argmax(mean_spec))
        peak_hz = float(f[peak_idx])
        peak_snr = float(mean_spec[peak_idx] / (np.median(mean_spec) + 1e-12))

        label = "NONE"
        event = "none"
        near_1x = self._near(peak_hz, shaft_hz)
        near_2x = self._near(peak_hz, 2.0 * shaft_hz)
        if n_stripes >= 1 and not near_1x and not near_2x:
            label = "BROADBAND_IMPULSE"
            event = "broadband_stripe"
        elif near_2x or (mean2 > mean1 * 1.15 and (peak_snr >= 3.0 or rise2 >= 1.8)):
            label = "2X_RPM"
            event = "2x_persistent"
        elif near_1x or (mean1 > mean2 and peak_snr >= 3.0) or rise1 >= 1.8:
            label = "1X_RPM"
            event = "1x_persistent"
        elif n_stripes >= 1:
            label = "BROADBAND_IMPULSE"
            event = "broadband_stripe"

        peaks = [{"frequency": round(peak_hz, 2), "amplitude": round(float(mean_spec[peak_idx]), 6), "band": "stft"}]
        return {
            "axis": axis,
            "label": label,
            "event": event,
            "primary_peak_hz": round(peak_hz, 2),
            "n_stripes": n_stripes,
            "band_1x": round(mean1, 6),
            "band_2x": round(mean2, 6),
            "peaks": peaks,
        }

    def _combine_axes(self, findings: List[Dict[str, Any]]) -> Tuple[str, str]:
        labels = [f["label"] for f in findings if f["label"] != "NONE"]
        events = [f["event"] for f in findings if f["event"] != "none"]
        stripe_axes = sum(1 for f in findings if f["event"] == "broadband_stripe")
        if stripe_axes >= 2:
            if "1X_RPM" not in labels and "2X_RPM" not in labels:
                return "BROADBAND_IMPULSE", "broadband_stripe"
        if labels.count("2X_RPM") >= labels.count("1X_RPM") and "2X_RPM" in labels:
            return "2X_RPM", "2x_persistent"
        if "1X_RPM" in labels:
            return "1X_RPM", "1x_persistent"
        if "BROADBAND_IMPULSE" in labels:
            return "BROADBAND_IMPULSE", "broadband_stripe"
        return "NONE", events[0] if events else "none"

    @staticmethod
    def _band_power(freqs: np.ndarray, mag: np.ndarray, target_hz: float) -> np.ndarray:
        tol = max(2.0, 0.04 * target_hz)
        mask = np.abs(freqs - target_hz) <= tol
        if not np.any(mask):
            return np.zeros(mag.shape[1], dtype=np.float64)
        return mag[mask, :].sum(axis=0)

    @staticmethod
    def _spectral_flatness(mag: np.ndarray) -> np.ndarray:
        safe = np.maximum(mag, 1e-12)
        geo = np.exp(np.mean(np.log(safe), axis=0))
        arith = np.mean(safe, axis=0)
        return geo / np.maximum(arith, 1e-12)

    @staticmethod
    def _near(freq_hz: float, target_hz: float) -> bool:
        return abs(freq_hz - target_hz) <= max(3.0, 0.05 * target_hz)
