"""0–50 Hz STFT spectrogram: eccentric load, misalignment, broadband stripes."""

import numpy as np

from src.agents.orchestrator import APMSOrchestrator
from src.signal_processing.spectrogram import VibrationSpectrogramEngine
from tests.test_pipeline import _frame


def _tone(hz: float, fs: float = 25600.0, duration: float = 1.0, amp: float = 1.5) -> np.ndarray:
    n = int(fs * duration)
    t = np.arange(n) / fs
    rng = np.random.default_rng(11)
    return amp * np.sin(2.0 * np.pi * hz * t) + 0.03 * rng.normal(size=n)


def _impulse_train(fs: float = 25600.0, duration: float = 1.0) -> np.ndarray:
    n = int(fs * duration)
    rng = np.random.default_rng(13)
    x = 0.04 * rng.normal(size=n)
    for k in (0.35, 0.55, 0.75):
        i = int(k * n)
        x[i:i + 8] += np.linspace(6.0, 0.0, 8)
    return x


def test_spectrogram_detects_1x_eccentric_load():
    rpm = 1480.0
    shaft = rpm / 60.0
    engine = VibrationSpectrogramEngine()
    result = engine.analyze(rpm=rpm, sample_rate_hz=25600.0, waveform=_tone(shaft).tolist())
    assert result is not None
    assert result["method"] == "stft_spectrogram"
    assert result["defect_label"] == "1X_RPM"
    assert result["spectrogram_event"] == "1x_persistent"


def test_spectrogram_detects_2x_misalignment():
    rpm = 1480.0
    engine = VibrationSpectrogramEngine()
    result = engine.analyze(rpm=rpm, sample_rate_hz=25600.0, waveform=_tone(2.0 * rpm / 60.0).tolist())
    assert result is not None
    assert result["defect_label"] == "2X_RPM"


def test_spectrogram_detects_broadband_stripe():
    engine = VibrationSpectrogramEngine()
    result = engine.analyze(rpm=1480.0, sample_rate_hz=25600.0, waveform=_impulse_train().tolist())
    assert result is not None
    assert result["defect_label"] == "BROADBAND_IMPULSE"
    assert result["matched_fault_code"] == "MF001_MECHANICAL_LOOSENESS"


def test_gamma_triaxial_spectrogram_eccentric_load():
    rpm = 1480.0
    shaft = rpm / 60.0
    x = _tone(shaft).tolist()
    orchestrator = APMSOrchestrator()
    response = orchestrator.run(_frame(
        imuAcceleration=6.8,
        waveformX=x,
        waveformY=x,
        waveformZ=x,
        sampleRateHz=25600.0,
        vibrationHarmonics=[],
    ))
    assert response.defect_localization is not None
    assert response.defect_localization.spectrum_source == "stft_spectrogram"
    assert response.defect_localization.defect_code == "MF003"
    assert response.defect_localization.spectrogram_event == "1x_persistent"
