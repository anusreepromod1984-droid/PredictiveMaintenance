"""
ISO 13373-2 Hilbert envelope FFT on a triggered raw waveform.
Falls back to matching pre-extracted H1–H5 peaks when no capture is present.
"""

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.signal import butter, decimate, find_peaks, hilbert, sosfiltfilt

from src.config import settings
from src.schemas.telemetry import HarmonicPeak
from src.utils.logger import get_logger

logger = get_logger("SignalProcessing.EnvelopeFFT")

FAULT_CODE_MAP = {
    "1X_RPM": "MF003_ROTOR_IMBALANCE",
    "2X_RPM": "MF002_SHAFT_MISALIGNMENT",
    "BPFI": "BPFI_BEARING_INNER_RACE_PITTING",
    "BPFO": "BPFO_BEARING_OUTER_RACE_CRACK",
    "BSF": "BSF_BALL_SPIN_WEAR",
    "FTF": "FTF_CAGE_WEAR",
    "NONE": "MECHANICAL_NORMAL",
}
# Envelope peaks identify bearing impacts; baseband FFT identifies 1X/2X.
MATCH_PRIORITY = {"BPFI": 6, "BPFO": 5, "BSF": 4, "FTF": 3, "2X_RPM": 2, "1X_RPM": 1}


class EnvelopeFFTEngine:
    def __init__(self, catalog_path: str = None):
        if catalog_path is None:
            catalog_path = os.path.join(os.path.dirname(__file__), "..", "data", "bearing_catalog.json")
        self.catalog = self._load_catalog(catalog_path)

    def _load_catalog(self, path: str) -> Dict[str, Any]:
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    return json.load(f).get("bearings", {})
        except Exception as exc:
            logger.error(f"Failed to load bearing catalog from {path}: {exc}")
        return {
            "SKF-6208": {
                "bpfi_multiplier": 5.43,
                "bpfo_multiplier": 3.57,
                "bsf_multiplier": 2.32,
                "ftf_multiplier": 0.40,
            }
        }

    def compute_bearing_defect_frequencies(self, rpm: float, bearing_model: str = "SKF-6208") -> Dict[str, float]:
        shaft_freq_hz = rpm / 60.0
        b_info = self.catalog.get(bearing_model, self.catalog.get("SKF-6208")) or {}
        return {
            "1X_RPM": shaft_freq_hz,
            "2X_RPM": shaft_freq_hz * 2.0,
            "BPFI": shaft_freq_hz * b_info.get("bpfi_multiplier", 5.43),
            "BPFO": shaft_freq_hz * b_info.get("bpfo_multiplier", 3.57),
            "BSF": shaft_freq_hz * b_info.get("bsf_multiplier", 2.32),
            "FTF": shaft_freq_hz * b_info.get("ftf_multiplier", 0.40),
        }

    def analyze(
        self,
        *,
        rpm: float,
        bearing_model: str = "SKF-6208",
        harmonics: Optional[Sequence[HarmonicPeak]] = None,
        waveform: Optional[Sequence[float]] = None,
        sample_rate_hz: Optional[float] = None,
    ) -> Dict[str, Any]:
        if self._waveform_usable(waveform, sample_rate_hz):
            try:
                return self.analyze_waveform(
                    waveform=waveform,
                    sample_rate_hz=sample_rate_hz or settings.WAVEFORM_SAMPLE_RATE_HZ,
                    rpm=rpm,
                    bearing_model=bearing_model,
                )
            except Exception as exc:
                logger.warning(f"Envelope FFT failed, falling back to H1–H5: {exc}")
        return self.analyze_harmonics(harmonics or [], rpm, bearing_model)

    def analyze_waveform(
        self,
        waveform: Sequence[float],
        sample_rate_hz: float,
        rpm: float,
        bearing_model: str = "SKF-6208",
    ) -> Dict[str, Any]:
        x = np.asarray(waveform, dtype=np.float64)
        if x.size > settings.ENVELOPE_MAX_SAMPLES:
            x = x[: settings.ENVELOPE_MAX_SAMPLES]
        x = x - np.mean(x)
        fs = float(sample_rate_hz)
        catalog = self.compute_bearing_defect_frequencies(rpm, bearing_model)

        env_freqs, env_mag = self._envelope_spectrum(x, fs)
        env_peaks = self._pick_peaks(env_freqs, env_mag, band="envelope")

        base_freqs, base_mag = self._baseband_spectrum(x, fs)
        base_peaks = self._pick_peaks(base_freqs, base_mag, band="baseband")

        # Envelope (bearing impacts) wins over baseband 1X/2X when both exist.
        matched = self._best_match(env_peaks, catalog) or self._best_match(base_peaks, catalog)
        primary = (env_peaks[0]["frequency"] if env_peaks else (base_peaks[0]["frequency"] if base_peaks else 0.0))
        peaks = (env_peaks or base_peaks)[:5]
        label = matched["label"] if matched else "NONE"

        logger.info(
            f"Hilbert envelope FFT n={x.size} fs={fs:.0f}Hz primary={primary:.1f}Hz "
            f"match={label} peaks={len(env_peaks)} env / {len(base_peaks)} baseband"
        )
        return self._result(
            primary_hz=primary,
            label=label,
            catalog=catalog,
            method="hilbert_envelope_fft",
            peaks=peaks,
            target_hz=matched["target_hz"] if matched else 0.0,
        )

    def analyze_harmonics(
        self,
        harmonics: Sequence[HarmonicPeak],
        rpm: float,
        bearing_model: str = "SKF-6208",
    ) -> Dict[str, Any]:
        catalog = self.compute_bearing_defect_frequencies(rpm, bearing_model)
        if not harmonics:
            return self._result(0.0, "NONE", catalog, "precomputed_harmonics", [])

        sorted_h = sorted(harmonics, key=lambda h: h.amplitude, reverse=True)
        primary_freq = sorted_h[0].frequency
        peaks = [{"frequency": round(h.frequency, 2), "amplitude": float(h.amplitude), "band": "precomputed"} for h in sorted_h[:5]]
        dummy = [{"frequency": primary_freq, "amplitude": 1.0, "snr": 1.0, "band": "precomputed"}]
        matched = self._best_match(dummy, catalog)
        label = matched["label"] if matched else "NONE"
        return self._result(
            primary_hz=primary_freq,
            label=label,
            catalog=catalog,
            method="precomputed_harmonics",
            peaks=peaks,
            target_hz=matched["target_hz"] if matched else 0.0,
        )

    def _waveform_usable(self, waveform: Optional[Sequence[float]], sample_rate_hz: Optional[float]) -> bool:
        if waveform is None:
            return False
        n = len(waveform)
        if n < settings.ENVELOPE_MIN_SAMPLES:
            return False
        fs = float(sample_rate_hz or settings.WAVEFORM_SAMPLE_RATE_HZ)
        return fs > 0

    def _envelope_spectrum(self, x: np.ndarray, fs: float) -> Tuple[np.ndarray, np.ndarray]:
        nyquist = fs / 2.0
        low = min(settings.ENVELOPE_BANDPASS_LOW_HZ, 0.15 * nyquist)
        high = min(settings.ENVELOPE_BANDPASS_HIGH_HZ, 0.45 * nyquist)
        if high > low + 50.0 and x.size > 64:
            sos = butter(4, [low / nyquist, high / nyquist], btype="bandpass", output="sos")
            x = sosfiltfilt(sos, x)
        envelope = np.abs(hilbert(x))
        envelope = envelope - np.mean(envelope)
        target_fs = min(settings.ENVELOPE_DECIMATE_HZ, fs)
        q = max(1, int(round(fs / target_fs)))
        if q > 1 and envelope.size // q >= 256:
            envelope = decimate(envelope, q, zero_phase=True)
            env_fs = fs / q
        else:
            env_fs = fs
        return self._rfft_mag(envelope, env_fs)

    def _baseband_spectrum(self, x: np.ndarray, fs: float) -> Tuple[np.ndarray, np.ndarray]:
        nyquist = fs / 2.0
        cutoff = min(400.0, 0.4 * nyquist)
        if cutoff > 2.0 and x.size > 64:
            sos = butter(4, cutoff / nyquist, btype="lowpass", output="sos")
            x = sosfiltfilt(sos, x)
        target_fs = min(2000.0, fs)
        q = max(1, int(round(fs / target_fs)))
        if q > 1 and x.size // q >= 256:
            x = decimate(x, q, zero_phase=True)
            fs = fs / q
        return self._rfft_mag(x, fs)

    def _rfft_mag(self, x: np.ndarray, fs: float) -> Tuple[np.ndarray, np.ndarray]:
        window = np.hanning(x.size)
        spec = np.fft.rfft(x * window)
        mag = np.abs(spec) * 2.0 / np.sum(window)
        freqs = np.fft.rfftfreq(x.size, d=1.0 / fs)
        return freqs, mag

    def _pick_peaks(
        self,
        freqs: np.ndarray,
        mag: np.ndarray,
        band: str,
    ) -> List[Dict[str, Any]]:
        fmax = settings.ENVELOPE_MAX_FREQ_HZ
        mask = (freqs >= 1.0) & (freqs <= fmax)
        if not np.any(mask):
            return []
        f = freqs[mask]
        m = mag[mask]
        floor = float(np.median(m))
        height = max(floor * 5.0, float(np.max(m)) * 0.12)
        df = float(f[1] - f[0]) if f.size > 1 else 1.0
        distance = max(1, int(round(2.0 / df)))
        idx, _ = find_peaks(m, height=height, distance=distance)
        if idx.size == 0:
            top = int(np.argmax(m))
            idx = np.array([top])
        ranked = sorted(idx.tolist(), key=lambda i: m[i], reverse=True)[:8]
        peaks = []
        for i in ranked:
            snr = float(m[i] / max(floor, 1e-12))
            peaks.append({
                "frequency": round(float(f[i]), 2),
                "amplitude": round(float(m[i]), 6),
                "snr": round(snr, 2),
                "band": band,
            })
        return peaks

    def _best_match(self, peaks: List[Dict[str, Any]], catalog: Dict[str, float]) -> Optional[Dict[str, Any]]:
        best = None
        best_score = 0.0
        for peak in peaks:
            freq = float(peak["frequency"])
            snr = float(peak.get("snr") or 1.0)
            band = peak.get("band", "envelope")
            if band != "precomputed" and snr < 6.0:
                continue
            for label, target in catalog.items():
                if band == "envelope" and label in {"1X_RPM", "2X_RPM"}:
                    continue
                if band == "baseband" and label in {"BPFI", "BPFO", "BSF", "FTF"}:
                    continue
                if band == "precomputed":
                    pass
                tol = max(3.0, 0.02 * target)
                diff = abs(freq - target)
                if diff > tol:
                    continue
                closeness = 1.0 - (diff / tol)
                score = snr * closeness * (1.0 + 0.15 * MATCH_PRIORITY.get(label, 0))
                if score > best_score:
                    best_score = score
                    best = {"label": label, "target_hz": round(target, 2), "diff_hz": round(diff, 2)}
        return best

    def _result(
        self,
        primary_hz: float,
        label: str,
        catalog: Dict[str, float],
        method: str,
        peaks: List[Dict[str, Any]],
        target_hz: float = 0.0,
    ) -> Dict[str, Any]:
        return {
            "primary_peak_hz": round(float(primary_hz), 2),
            "matched_target_hz": target_hz or round(catalog.get(label, 0.0), 2),
            "matched_fault_code": FAULT_CODE_MAP.get(label, "MF001_MECHANICAL_LOOSENESS"),
            "defect_label": label,
            "theoretical_frequencies": {k: round(v, 2) for k, v in catalog.items()},
            "method": method,
            "peaks": peaks,
        }
