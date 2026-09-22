"""Hilbert envelope FFT on triggered raw waveforms (ISO 13373-2)."""

import numpy as np

from src.agents.orchestrator import APMSOrchestrator
from src.schemas.telemetry import HarmonicPeak
from src.signal_processing.envelope_fft import EnvelopeFFTEngine
from tests.test_pipeline import _frame


def _am_bearing_waveform(rpm: float, multiplier: float, fs: float = 25600.0, duration: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(7)
    n = int(fs * duration)
    t = np.arange(n) / fs
    shaft = rpm / 60.0
    mod_hz = shaft * multiplier
    carrier_hz = 7000.0
    envelope = 1.0 + 0.7 * np.cos(2.0 * np.pi * mod_hz * t)
    x = envelope * np.sin(2.0 * np.pi * carrier_hz * t)
    x += 0.15 * np.sin(2.0 * np.pi * shaft * t)
    x += 0.04 * rng.normal(size=n)
    return x


def test_envelope_fft_detects_bpfi_from_raw_waveform():
    rpm = 1480.0
    engine = EnvelopeFFTEngine()
    catalog = engine.compute_bearing_defect_frequencies(rpm, "SKF-6208")
    waveform = _am_bearing_waveform(rpm, 5.43)
    result = engine.analyze(
        rpm=rpm,
        bearing_model="SKF-6208",
        waveform=waveform.tolist(),
        sample_rate_hz=25600.0,
    )
    assert result["method"] == "hilbert_envelope_fft"
    assert result["defect_label"] == "BPFI"
    assert abs(result["primary_peak_hz"] - catalog["BPFI"]) < 4.0


def test_envelope_fft_detects_bpfo_from_raw_waveform():
    rpm = 1480.0
    engine = EnvelopeFFTEngine()
    waveform = _am_bearing_waveform(rpm, 3.57)
    result = engine.analyze(
        rpm=rpm,
        bearing_model="SKF-6208",
        waveform=waveform.tolist(),
        sample_rate_hz=25600.0,
    )
    assert result["defect_label"] == "BPFO"


def test_baseband_fft_detects_2x_misalignment():
    fs = 25600.0
    rpm = 1480.0
    n = int(fs)
    t = np.arange(n) / fs
    shaft = rpm / 60.0
    x = 1.8 * np.sin(2.0 * np.pi * 2.0 * shaft * t)
    x += 0.03 * np.random.default_rng(3).normal(size=n)
    engine = EnvelopeFFTEngine()
    result = engine.analyze(rpm=rpm, waveform=x.tolist(), sample_rate_hz=fs)
    assert result["defect_label"] == "2X_RPM"
    assert result["matched_fault_code"] == "MF002_SHAFT_MISALIGNMENT"


def test_short_waveform_falls_back_to_harmonics():
    engine = EnvelopeFFTEngine()
    result = engine.analyze(
        rpm=1480,
        waveform=[0.1, 0.2, 0.3],
        sample_rate_hz=25600,
        harmonics=[HarmonicPeak(frequency=49.3, amplitude=-8.0)],
    )
    assert result["method"] == "precomputed_harmonics"
    assert result["defect_label"] == "2X_RPM"


def test_gamma_uses_waveform_spectrum_source():
    rpm = 1480.0
    waveform = _am_bearing_waveform(rpm, 5.43)
    orchestrator = APMSOrchestrator()
    response = orchestrator.run(_frame(
        imuAcceleration=6.8,
        waveform=waveform.tolist(),
        sampleRateHz=25600.0,
        vibrationHarmonics=[],
    ))
    assert response.defect_localization is not None
    assert response.defect_localization.spectrum_source == "hilbert_envelope_fft"
    assert response.defect_localization.defect_code == "BPFI"
    assert response.defect_localization.dominant_frequencies_hz
