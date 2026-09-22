"""
CWRU (Case Western Reserve University) Bearing Fault Dataset Ingestor
======================================================================
Source  : https://engineering.case.edu/bearingdatacenter/download-data-file
Bearings: SKF 6205-2RS JEM  (Drive End)   — 12 kHz & 48 kHz sample rates
          SKF 6203-2RS JEM  (Fan End)     — 12 kHz sample rate
Fault classes ingested → our FaultLabel schema:
    CWRU "Normal"        → NORMAL
    CWRU "Inner Race"    → BPFI
    CWRU "Outer Race"    → BPFO
    CWRU "Ball"          → BPFO  (ball defect → outer-race-like spectral pattern)
    CWRU "Outer+Ball"    → BPFO  (combined)

Usage (standalone):
    python -m src.mlops.datasets.cwru_ingestor
or from code:
    from src.mlops.datasets.cwru_ingestor import run
    summary = run()
"""

from __future__ import annotations

import io
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("MLops.CWRU")

# ---------------------------------------------------------------------------
# CWRU download manifest
# Each entry: (url, label, bearing_model, shaft_rpm, fault_size_inch)
# Downloaded from official CWRU Bearing Data Center (open access).
# Mapping: 97=Normal, 105/169/209=IR, 130/197/234=OR@6, 118/185/222=Ball
# ---------------------------------------------------------------------------
CWRU_MANIFEST: List[Tuple[str, str, str, float, float]] = [
    # Normal baseline — 1797 RPM, DE bearing SKF 6205
    ("https://engineering.case.edu/sites/default/files/97.mat",   "NORMAL", "SKF-6205", 1797.0, 0.0),
    # Inner Race fault — Drive End (DE), 0.007" damage, 1797 RPM
    ("https://engineering.case.edu/sites/default/files/105.mat",  "BPFI",   "SKF-6205", 1797.0, 0.007),
    # Inner Race fault — 0.014"
    ("https://engineering.case.edu/sites/default/files/169.mat",  "BPFI",   "SKF-6205", 1797.0, 0.014),
    # Inner Race fault — 0.021"
    ("https://engineering.case.edu/sites/default/files/209.mat",  "BPFI",   "SKF-6205", 1797.0, 0.021),
    # Outer Race fault — centred @ 6 o'clock, 0.007"
    ("https://engineering.case.edu/sites/default/files/130.mat",  "BPFO",   "SKF-6205", 1797.0, 0.007),
    # Outer Race fault — 0.014"
    ("https://engineering.case.edu/sites/default/files/197.mat",  "BPFO",   "SKF-6205", 1797.0, 0.014),
    # Outer Race fault — 0.021"
    ("https://engineering.case.edu/sites/default/files/234.mat",  "BPFO",   "SKF-6205", 1797.0, 0.021),
    # Ball defect — 0.007"
    ("https://engineering.case.edu/sites/default/files/118.mat",  "BPFO",   "SKF-6205", 1797.0, 0.007),
    # Ball defect — 0.014"
    ("https://engineering.case.edu/sites/default/files/185.mat",  "BPFO",   "SKF-6205", 1797.0, 0.014),
    # Ball defect — 0.021"
    ("https://engineering.case.edu/sites/default/files/222.mat",  "BPFO",   "SKF-6205", 1797.0, 0.021),
]

# CWRU SKF 6205 bearing kinematic coefficients (9 balls, 39° contact angle, d/D ≈ 0.4)
CWRU_BEARING_FREQS = {
    "SKF-6205": {
        "bpfi_multiplier": 5.415,   # (N/2)*(1 + d/D*cos(α))  — Inner race pass
        "bpfo_multiplier": 3.585,   # (N/2)*(1 - d/D*cos(α))  — Outer race pass
        "bsf_multiplier":  2.357,   # (D/2d)*(1 - (d/D*cos(α))²) — Ball spin
        "ftf_multiplier":  0.398,   # (1/2)*(1 - d/D*cos(α))   — Cage / FTF
    }
}

SAMPLE_RATE_HZ = 12_000   # 12 kHz channel used for all standard CWRU files
WINDOW_SIZE    = 4096      # ~0.34 s at 12 kHz — one analysis window
WINDOW_STRIDE  = 2048      # 50% overlap
BANDPASS_LOW   = 2000      # Envelope highpass (Hz) — strips shaft harmonics
BANDPASS_HIGH  = 5500      # Envelope lowpass (Hz)


# ---------------------------------------------------------------------------
# Signal feature extraction (dimensionless, RPM-invariant)
# ---------------------------------------------------------------------------

def _hilbert_envelope(signal: np.ndarray) -> np.ndarray:
    """Return the analytic envelope of the signal via Hilbert transform."""
    from scipy.signal import hilbert
    return np.abs(hilbert(signal))


def _bandpass(signal: np.ndarray, low: float, high: float, fs: float) -> np.ndarray:
    """Simple 4th-order Butterworth bandpass filter."""
    from scipy.signal import butter, sosfiltfilt
    sos = butter(4, [low, high], btype="bandpass", fs=fs, output="sos")
    return sosfiltfilt(sos, signal)


def _extract_features(
    window: np.ndarray,
    rpm: float,
    fs: float,
    bearing_key: str,
) -> Dict[str, float]:
    """
    Extracts dimensionless, RPM-normalised features from one vibration window.

    Feature design principle (market giant standard — SKF OnCare, Emerson AMS):
    • All spectral features expressed as ORDER RATIOS (peak energy / total energy),
      not raw Hz, so the same XGBoost classifier works on 720 RPM & 1797 RPM machines.
    • Statistical moments (kurtosis, crest factor) are dimensionless by definition.
    """
    shaft_hz = rpm / 60.0
    mults = CWRU_BEARING_FREQS.get(bearing_key, CWRU_BEARING_FREQS["SKF-6205"])

    # ---- 1. Time-domain statistical moments --------------------------------
    rms        = float(np.sqrt(np.mean(window ** 2)))
    peak       = float(np.max(np.abs(window)))
    kurtosis   = float(np.mean(((window - window.mean()) / (window.std() + 1e-12)) ** 4))
    crest      = peak / (rms + 1e-12)
    skewness   = float(np.mean(((window - window.mean()) / (window.std() + 1e-12)) ** 3))

    # ---- 2. Envelope spectrum energy at bearing defect frequencies ---------
    try:
        bandpassed = _bandpass(window, BANDPASS_LOW, BANDPASS_HIGH, fs)
        envelope   = _hilbert_envelope(bandpassed)

        freqs   = np.fft.rfftfreq(len(envelope), d=1.0 / fs)
        env_fft = np.abs(np.fft.rfft(envelope - envelope.mean())) / len(envelope)
        total_e = np.sum(env_fft ** 2) + 1e-12

        def _order_energy(target_hz: float, bw_hz: float = 3.0) -> float:
            mask = (freqs >= target_hz - bw_hz) & (freqs <= target_hz + bw_hz)
            return float(np.sum(env_fft[mask] ** 2) / total_e)

        bpfi_e  = _order_energy(shaft_hz * mults["bpfi_multiplier"])
        bpfo_e  = _order_energy(shaft_hz * mults["bpfo_multiplier"])
        bsf_e   = _order_energy(shaft_hz * mults["bsf_multiplier"])
        ftf_e   = _order_energy(shaft_hz * mults["ftf_multiplier"])
        one_x_e = _order_energy(shaft_hz)
        two_x_e = _order_energy(shaft_hz * 2.0)

    except Exception:
        bpfi_e = bpfo_e = bsf_e = ftf_e = one_x_e = two_x_e = 0.0

    return {
        # Raw amplitude proxy
        "imuAcceleration": round(rms, 5),
        # Statistical moments (dimensionless)
        "kurtosis":        round(kurtosis, 4),
        "crestFactor":     round(crest, 4),
        "skewness":        round(skewness, 4),
        # Order energy ratios (dimensionless — RPM-invariant)
        "energyBpfi":      round(bpfi_e, 6),
        "energyBpfo":      round(bpfo_e, 6),
        "energyBsf":       round(bsf_e, 6),
        "energyFtf":       round(ftf_e, 6),
        "energy1x":        round(one_x_e, 6),
        "energy2x":        round(two_x_e, 6),
        # Metadata for diagnostics
        "shaftRpm":        round(rpm, 1),
        "bearingModel":    bearing_key,
        # Classifier-compatible legacy keys (keeps backward compat with fit_classifier.py)
        "emVoltageImbalance": 0.0,
        "emMachineLoad":      70.0,
        "tempMotor":          45.0,
        "tempAmbient":        25.0,
        "pattern":            "STABLE",
        "isolatedDomain":     "MECHANICAL",
        "matchedFault":       "BPFI" if bpfi_e > bpfo_e else ("BPFO" if bpfo_e > 0.001 else "NONE"),
    }


# ---------------------------------------------------------------------------
# .mat file reader (no MATLAB required — pure scipy)
# ---------------------------------------------------------------------------

def _load_mat_channel(raw_bytes: bytes, rpm: float) -> Optional[np.ndarray]:
    """
    Parse a CWRU .mat file and extract the Drive End (DE) accelerometer channel.
    CWRU files contain keys like 'X097_DE_time', 'X105_DE_time', etc.
    Falls back to any key containing 'DE_time' or 'time'.
    """
    try:
        import scipy.io as sio
        mat = sio.loadmat(io.BytesIO(raw_bytes))
    except Exception as exc:
        logger.error("Failed to parse .mat file: %s", exc)
        return None

    # Find Drive End accelerometer channel
    for key in mat:
        if key.startswith("_"):
            continue
        if "DE_time" in key:
            arr = mat[key].flatten().astype(np.float64)
            logger.debug("CWRU channel '%s' shape=%s", key, arr.shape)
            return arr

    # Fallback: any 1D float array longer than 1000 points
    for key in mat:
        if key.startswith("_"):
            continue
        val = mat[key]
        if hasattr(val, "flatten") and val.size > 1000:
            return val.flatten().astype(np.float64)

    return None


# ---------------------------------------------------------------------------
# Download + segment + convert → inbox/labeled_windows.jsonl
# ---------------------------------------------------------------------------

def _download_bytes(url: str, timeout: int = 60) -> Optional[bytes]:
    """Download a file from URL. Returns None on failure."""
    try:
        logger.info("  Downloading %s …", url.split("/")[-1])
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:
        logger.warning("  Download failed for %s: %s", url, exc)
        return None


def _windows_from_signal(
    signal: np.ndarray,
    rpm: float,
    bearing_key: str,
    label: str,
    machine_id: str = "CWRU_BENCHMARK",
    max_windows: int = 80,
) -> List[Dict[str, Any]]:
    """Slide a window over the raw vibration signal and extract features."""
    records = []
    n = len(signal)
    step = WINDOW_STRIDE
    positions = range(0, n - WINDOW_SIZE, step)

    # Sub-sample to at most max_windows evenly spaced positions
    if len(positions) > max_windows:
        idxs = np.linspace(0, len(positions) - 1, max_windows, dtype=int)
        positions = [list(positions)[i] for i in idxs]

    for pos in positions:
        window = signal[pos: pos + WINDOW_SIZE]
        if len(window) < WINDOW_SIZE:
            break
        feats = _extract_features(window, rpm, float(SAMPLE_RATE_HZ), bearing_key)
        ts = datetime.now(timezone.utc).isoformat()
        records.append({
            "machineId": machine_id,
            "timestamp":  ts,
            "label":      label,
            "source":     "teardown",
            "features":   feats,
            "notes":      f"CWRU benchmark — {bearing_key} — {label}",
        })

    return records


def run(
    cache_dir: Optional[Path] = None,
    max_windows_per_file: int = 80,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Download CWRU benchmark files, extract features, write to inbox.

    Args:
        cache_dir: Directory to cache downloaded .mat files (avoids re-download).
                   Defaults to data/training/cache/cwru/.
        max_windows_per_file: Max sliding-window samples extracted per .mat file.
        use_cache: If True (default), skip re-downloading existing cached files.

    Returns:
        Summary dict with counts per label and total windows written.
    """
    from src.mlops.store import add_labeled_window, inbox_dir
    from src.schemas.training import LabeledWindow

    if cache_dir is None:
        # Resolve relative to project root (two levels up from this file)
        cache_dir = Path(__file__).resolve().parents[4] / "data" / "training" / "cache" / "cwru"
    cache_dir.mkdir(parents=True, exist_ok=True)

    total_written = 0
    counts: Dict[str, int] = {}

    for url, label, bearing, rpm, fault_size in CWRU_MANIFEST:
        filename = url.split("/")[-1]
        cache_path = cache_dir / filename

        # 1. Load from cache or download
        if use_cache and cache_path.exists():
            logger.info("[CWRU] Cache hit: %s", filename)
            raw = cache_path.read_bytes()
        else:
            raw = _download_bytes(url)
            if raw is None:
                logger.warning("[CWRU] Skipping %s — download failed.", filename)
                continue
            cache_path.write_bytes(raw)

        # 2. Parse .mat → numpy signal
        signal = _load_mat_channel(raw, rpm)
        if signal is None or len(signal) < WINDOW_SIZE:
            logger.warning("[CWRU] No usable DE channel in %s — skipping.", filename)
            continue

        # 3. Slide windows and extract features
        records = _windows_from_signal(
            signal, rpm, bearing, label,
            machine_id=f"CWRU_BENCHMARK_{label}_{fault_size:.3f}in",
            max_windows=max_windows_per_file,
        )

        # 4. Write to inbox via store
        for rec in records:
            try:
                window = LabeledWindow(
                    machineId=rec["machineId"],
                    timestamp=rec["timestamp"],
                    label=rec["label"],
                    source=rec["source"],
                    features=rec["features"],
                    notes=rec["notes"],
                )
                add_labeled_window(window)
                total_written += 1
                counts[label] = counts.get(label, 0) + 1
            except Exception as exc:
                logger.warning("[CWRU] Failed to write window: %s", exc)

        logger.info(
            "[CWRU] %s → label=%s  windows=%d  (total so far: %d)",
            filename, label, len(records), total_written,
        )

    summary = {
        "dataset":       "CWRU",
        "total_windows": total_written,
        "by_label":      counts,
        "cache_dir":     str(cache_dir),
        "status":        "ok" if total_written > 0 else "no_data_downloaded",
    }
    logger.info("[CWRU] Ingestion complete: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    result = run()
    print("\n=== CWRU Ingestion Summary ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
