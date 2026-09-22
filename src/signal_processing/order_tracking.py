"""
order_tracking.py — Shaft-Speed Normalised (Order Domain) Vibration Analysis
=============================================================================
This module solves the fundamental fleet-scaling problem:
    A fault classifier trained on CWRU data at 1797 RPM
    must correctly classify the same bearing fault at 720 RPM (our Elson EL30)
    or at 2980 RPM (a large pump in an expanded plant).

Solution: Order Domain Analysis
    Instead of tracking peaks at fixed Hz (e.g., BPFO = 59.3 Hz at 1797 RPM),
    we normalise the frequency axis to multiples of shaft rotation speed:
        Order N = frequency / shaft_frequency_Hz
    So BPFO at any RPM always falls at the same order number (e.g., 3.585×).
    The XGBoost classifier sees dimensionless ORDER ENERGY RATIOS, not raw Hz.
    This makes one trained model valid across ALL RPM values.

References:
  • ISO 13373-2 :2016 Machinery Vibration — Condition monitoring
  • SKF @ptitude Analyst: "Order Tracking" chapter
  • Emerson AMS Asset Monitor: "Cross-Machine Signature Analysis"
  • Bently Nevada System 1: "Full Spectrum Order Plots"
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Bearing kinematic order multipliers
# (dimensionless — RPM invariant by definition)
# ---------------------------------------------------------------------------

STANDARD_ORDERS = {
    "1X":  1.0,       # Shaft rotational frequency (imbalance, eccentric mass)
    "2X":  2.0,       # Twice shaft (misalignment, bent shaft, looseness)
    "3X":  3.0,       # Three times (looseness, multi-vane effects)
    "0.5X": 0.5,      # Sub-harmonic (fluid-film instability, oil whirl)
}


class OrderTrackingEngine:
    """
    Computes dimensionless order-domain spectral features from raw vibration
    waveform data or from FFT magnitude arrays.

    Compatible with our existing EnvelopeFFTEngine output and also directly
    with raw waveforms from CWRU, PRONOSTIA, and NASA IMS datasets.

    Usage:
        engine = OrderTrackingEngine(bearing_catalog)
        features = engine.extract_order_features(
            waveform=signal,
            sample_rate_hz=12000,
            shaft_rpm=720.0,
            bearing_model="SKF-6206",
        )
    """

    def __init__(self, bearing_catalog: Optional[Dict[str, Any]] = None):
        """
        Args:
            bearing_catalog: Dict of bearing defect-frequency multipliers.
                             Keys are bearing model names (e.g. "SKF-6206").
                             If None, loads from default bearing_catalog.json.
        """
        if bearing_catalog is None:
            bearing_catalog = self._load_default_catalog()
        self.catalog = bearing_catalog

    @staticmethod
    def _load_default_catalog() -> Dict[str, Any]:
        """Load bearing multipliers from the project bearing_catalog.json."""
        import json
        import os
        catalog_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "bearing_catalog.json"
        )
        try:
            with open(catalog_path, "r") as f:
                return json.load(f).get("bearings", {})
        except Exception:
            # Minimal fallback covering our Elson EL30 bearing
            return {
                "SKF-6206": {
                    "bpfi_multiplier": 4.94,
                    "bpfo_multiplier": 3.06,
                    "bsf_multiplier":  1.97,
                    "ftf_multiplier":  0.382,
                },
                "SKF-6205": {
                    "bpfi_multiplier": 5.415,
                    "bpfo_multiplier": 3.585,
                    "bsf_multiplier":  2.357,
                    "ftf_multiplier":  0.398,
                },
            }

    def get_bearing_orders(self, bearing_model: str) -> Dict[str, float]:
        """
        Return defect frequency ORDERS (dimensionless multipliers) for a bearing.
        These are the same at any RPM — the shaft frequency cancels out.
        """
        mults = self.catalog.get(bearing_model, {})
        if not mults:
            # Fallback to SKF-6206 if model not found
            mults = self.catalog.get("SKF-6206", {})
        return {
            "1X":   1.0,
            "2X":   2.0,
            "BPFI": mults.get("bpfi_multiplier", 5.0),
            "BPFO": mults.get("bpfo_multiplier", 3.5),
            "BSF":  mults.get("bsf_multiplier",  2.3),
            "FTF":  mults.get("ftf_multiplier",  0.4),
        }

    def extract_order_features(
        self,
        waveform: np.ndarray,
        sample_rate_hz: float,
        shaft_rpm: float,
        bearing_model: str = "SKF-6206",
        envelope_bandpass: Tuple[float, float] = (2000.0, 8000.0),
        order_bandwidth: float = 0.15,   # ±0.15 orders around each target order
    ) -> Dict[str, float]:
        """
        Extract dimensionless, RPM-invariant order-domain features.

        Args:
            waveform:           Raw vibration time-series (in g or mm/s).
            sample_rate_hz:     Acquisition sample rate (Hz).
            shaft_rpm:          Actual shaft rotation speed (RPM).
            bearing_model:      Bearing ID key in catalog (e.g. "SKF-6206").
            envelope_bandpass:  (low_hz, high_hz) for bandpass before envelope.
            order_bandwidth:    ±bandwidth in orders around each defect order.

        Returns:
            Dict of dimensionless features usable directly in XGBoost / PINN.

        Design rationale:
            ALL spectral features are expressed as RATIOS (energy at target order /
            total spectrum energy). This means a ball defect at 720 RPM and at
            1797 RPM both produce the same "energyBpfo" value if the physical
            damage severity is the same — enabling fleet-wide transfer learning.
        """
        shaft_hz   = shaft_rpm / 60.0
        n          = len(waveform)

        # ---- 1. Time-domain statistics (always dimensionless) ---------------
        rms      = float(np.sqrt(np.mean(waveform ** 2)))
        peak     = float(np.max(np.abs(waveform)))
        std      = waveform.std()
        kurtosis = float(np.mean(((waveform - waveform.mean()) / (std + 1e-12)) ** 4))
        crest    = peak / (rms + 1e-12)
        skewness = float(np.mean(((waveform - waveform.mean()) / (std + 1e-12)) ** 3))

        # ---- 2. Compute baseband FFT (for 1X/2X shaft harmonics) ------------
        freqs_bb = np.fft.rfftfreq(n, d=1.0 / sample_rate_hz)
        fft_bb   = np.abs(np.fft.rfft(waveform)) / n

        # ---- 3. Envelope spectrum (for bearing defect orders) ----------------
        try:
            from scipy.signal import butter, sosfiltfilt, hilbert
            low_hz, high_hz = envelope_bandpass
            # Clamp bandpass to valid range for this sample rate
            nyq = sample_rate_hz / 2.0
            low_hz  = min(low_hz,  nyq * 0.9)
            high_hz = min(high_hz, nyq * 0.95)
            if low_hz >= high_hz:
                raise ValueError("Invalid bandpass range after clamping")
            sos        = butter(4, [low_hz, high_hz], btype="bandpass", fs=sample_rate_hz, output="sos")
            bp_signal  = sosfiltfilt(sos, waveform)
            envelope   = np.abs(hilbert(bp_signal))
            env_detrended = envelope - envelope.mean()
            freqs_env  = np.fft.rfftfreq(len(env_detrended), d=1.0 / sample_rate_hz)
            fft_env    = np.abs(np.fft.rfft(env_detrended)) / len(env_detrended)
            total_env_e = np.sum(fft_env ** 2) + 1e-12
            envelope_ok = True
        except Exception:
            freqs_env   = freqs_bb
            fft_env     = fft_bb
            total_env_e = np.sum(fft_env ** 2) + 1e-12
            envelope_ok = False

        # ---- 4. Order energy calculation (RPM-invariant) ---------------------
        orders      = self.get_bearing_orders(bearing_model)
        order_energies: Dict[str, float] = {}
        total_bb_e  = np.sum(fft_bb ** 2) + 1e-12

        for order_name, order_mult in orders.items():
            # Target frequency at current RPM
            target_hz  = shaft_hz * order_mult
            bw_hz      = shaft_hz * order_bandwidth   # bandwidth scales with RPM

            # Use envelope spectrum for bearing-type orders (BPFI, BPFO, BSF, FTF)
            # Use baseband FFT for shaft orders (1X, 2X)
            if order_name in ("1X", "2X"):
                freqs_use = freqs_bb
                fft_use   = fft_bb
                norm      = total_bb_e
            else:
                freqs_use = freqs_env
                fft_use   = fft_env
                norm      = total_env_e

            mask = (freqs_use >= target_hz - bw_hz) & (freqs_use <= target_hz + bw_hz)
            energy_ratio = float(np.sum(fft_use[mask] ** 2) / norm)
            order_energies[order_name] = round(energy_ratio, 7)

        # ---- 5. Spectral entropy (overall randomness / health metric) --------
        ps = fft_bb ** 2 / (total_bb_e)
        ps = np.where(ps > 0, ps, 1e-15)
        spectral_entropy = float(-np.sum(ps * np.log2(ps)))

        # ---- 6. Compose output feature dict -----------------------------------
        return {
            # Time-domain (dimensionless)
            "imuAcceleration": round(rms, 5),
            "kurtosis":        round(kurtosis, 4),
            "crestFactor":     round(crest, 4),
            "skewness":        round(skewness, 4),
            "spectralEntropy": round(spectral_entropy, 4),

            # Order domain energy ratios (RPM-invariant, dimensionless)
            "energy1x":    order_energies.get("1X",   0.0),
            "energy2x":    order_energies.get("2X",   0.0),
            "energyBpfi":  order_energies.get("BPFI", 0.0),
            "energyBpfo":  order_energies.get("BPFO", 0.0),
            "energyBsf":   order_energies.get("BSF",  0.0),
            "energyFtf":   order_energies.get("FTF",  0.0),

            # Diagnostics / metadata
            "shaftRpm":        round(shaft_rpm, 1),
            "shaftFreqHz":     round(shaft_hz, 3),
            "bearingModel":    bearing_model,
            "envelopeOk":      int(envelope_ok),

            # Legacy keys — backward compatible with fit_classifier.py FEATURE_KEYS
            "emVoltageImbalance": 0.0,
            "emMachineLoad":      70.0,
            "tempMotor":          45.0,
            "tempAmbient":        25.0,
            "deltaTemp":          20.0,
            "pattern":            "STABLE",
            "isolatedDomain":     "MECHANICAL",
            # Auto-infer matchedFault from highest energy ratio
            "matchedFault": _infer_matched_fault(order_energies),
        }


def _infer_matched_fault(order_energies: Dict[str, float]) -> str:
    """Select the highest-energy defect order as the 'matchedFault' label."""
    priority_order = ["BPFI", "BPFO", "BSF", "FTF", "2X", "1X"]
    # Only flag if energy ratio is meaningfully elevated (> 0.005 = 0.5% of energy)
    threshold = 0.005
    best = "NONE"
    best_e = 0.0
    for name in priority_order:
        e = order_energies.get(name, 0.0)
        if e > threshold and e > best_e:
            best   = name
            best_e = e
    return best


# ---------------------------------------------------------------------------
# Convenience: apply order tracking to a full CWRU / NASA IMS signal array
# and produce a list of windowed feature dicts ready for LabeledWindow.
# ---------------------------------------------------------------------------

def extract_windowed_order_features(
    signal: np.ndarray,
    sample_rate_hz: float,
    shaft_rpm: float,
    bearing_model: str,
    window_size: int = 4096,
    window_stride: int = 2048,
    max_windows: int = 80,
    bandpass: Tuple[float, float] = (2000.0, 8000.0),
) -> List[Dict[str, float]]:
    """
    Slide a window over a vibration signal and extract order features.

    Returns a list of feature dicts (one per window), ready to be wrapped in
    LabeledWindow and sent to the store inbox.
    """
    engine = OrderTrackingEngine()
    n      = len(signal)
    positions = range(0, n - window_size, window_stride)

    if len(list(positions)) > max_windows:
        idxs = np.linspace(0, len(list(positions)) - 1, max_windows, dtype=int)
        positions = [list(positions)[i] for i in idxs]

    results = []
    for pos in positions:
        window = signal[pos: pos + window_size]
        if len(window) < window_size:
            break
        try:
            feats = engine.extract_order_features(
                waveform=window,
                sample_rate_hz=sample_rate_hz,
                shaft_rpm=shaft_rpm,
                bearing_model=bearing_model,
                envelope_bandpass=bandpass,
            )
            results.append(feats)
        except Exception:
            continue

    return results
