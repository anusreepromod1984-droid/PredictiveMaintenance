"""
NASA IMS (Institute for Machine Science) Bearing Run-to-Failure Dataset Ingestor
=================================================================================
Source  : https://ti.arc.nasa.gov/tech/dash/groups/pcoe/prognostic-data-repository/
          (Kaggle mirror: https://www.kaggle.com/datasets/vinayak123tyagi/bearing-dataset)
Paper   : "Bearing Data Set" — Qiu, H., Lee, J., Lin, J., Yu, G. (2006)

Rig:      High-speed shaft (2000 RPM), 6000 lbs radial load
          4 bearings on one shaft, each with 2 accelerometers (x + y axis)
          Data sampled at 20 kHz, 1-second captures every 10 minutes

Datasets:
  Set 1  — 3 test files (bearing 1–4 each), ran 35 million revolutions
  Set 2  — 984 files, bearing 3 outer-race failure
  Set 3  — 4448 files, bearing 3 outer-race failure

We ingest Set 2 and Set 3 as RulTrajectory records (largest and most studied).

Failure labels (based on teardown analysis reported in the NASA paper):
  Set 1: Bearing 3 + 4 — inner race failure after ~35M revolutions
  Set 2: Bearing 1    — outer race failure
  Set 3: Bearing 3    — outer race failure

Download note: NASA IMS dataset requires Prognostics Center registration or is
available via Kaggle API. We provide a local-directory ingestion path for when
the user has downloaded manually, plus a Kaggle API download path.
"""

from __future__ import annotations

import csv
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("MLops.NASA_IMS")

# ---------------------------------------------------------------------------
# NASA IMS — Rexnord ZA-2115 double-row bearing kinematic data
# 16 rolling elements, contact angle 15.17°, d/D ≈ 0.331
# ---------------------------------------------------------------------------
IMS_BEARING_MULTS = {
    "bpfi_multiplier": 5.415,   # (N/2)*(1 + d/D*cos(α))
    "bpfo_multiplier": 3.585,   # (N/2)*(1 - d/D*cos(α))
    "bsf_multiplier":  2.357,
    "ftf_multiplier":  0.380,
}
IMS_BEARING_KEY  = "REXNORD-ZA2115"
IMS_SAMPLE_RATE  = 20_000   # 20 kHz per capture file
IMS_CAPTURE_S    = 1.0      # Each file = 1 second of vibration
IMS_INTERVAL_MIN = 10.0     # Files captured every 10 minutes
IMS_RPM          = 2000.0

# IMS file format: rows of space-delimited float values, 8 columns
# Columns: [Ch1 B1-X, Ch2 B1-Y, Ch3 B2-X, Ch4 B2-Y, Ch5 B3-X, Ch6 B3-Y, Ch7 B4-X, Ch8 B4-Y]
IMS_CHANNEL_MAP = {
    "bearing1_x": 0, "bearing1_y": 1,
    "bearing2_x": 2, "bearing2_y": 3,
    "bearing3_x": 4, "bearing3_y": 5,
    "bearing4_x": 6, "bearing4_y": 7,
}

# Failure threshold: RMS > 3g indicates impending failure
IMS_FAILURE_RMS_G = 3.0


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def _compute_features_from_channels(
    ch_x: np.ndarray,
    ch_y: np.ndarray,
    rpm: float = IMS_RPM,
) -> Dict[str, float]:
    """Combine x+y channels, compute dimensionless time-domain features."""
    combined = np.sqrt(ch_x ** 2 + ch_y ** 2)
    rms = float(np.sqrt(np.mean(combined ** 2)))
    std = combined.std()
    if std < 1e-12:
        kurt = 0.0
        crest = 0.0
    else:
        kurt  = float(np.mean(((combined - combined.mean()) / std) ** 4))
        crest = float(np.max(np.abs(combined))) / (rms + 1e-12)

    return {
        "rms_g":      rms,
        "kurtosis":   kurt,
        "crestFactor": crest,
        "shaftRpm":   rpm,
    }


# ---------------------------------------------------------------------------
# File parsing — IMS files are space/tab-delimited, no header
# ---------------------------------------------------------------------------

def _parse_ims_file(path: Path) -> Optional[np.ndarray]:
    """
    Parse one NASA IMS data file.
    Returns numpy array of shape (n_samples, 8), or None on failure.
    """
    try:
        data = np.loadtxt(str(path), delimiter="\t", dtype=np.float64)
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        if data.shape[0] < 100:
            return None
        return data
    except Exception:
        try:
            # Some IMS files use space delimiters
            data = np.loadtxt(str(path), dtype=np.float64)
            return data if data.ndim == 2 and data.shape[0] > 100 else None
        except Exception as exc:
            logger.debug("[NASA_IMS] Could not parse %s: %s", path.name, exc)
            return None


# ---------------------------------------------------------------------------
# Trajectory builder from a dataset directory
# ---------------------------------------------------------------------------

def _build_trajectory_from_dir(
    data_dir: Path,
    traj_id: str,
    bearing_col_x: int,
    bearing_col_y: int,
    rpm: float = IMS_RPM,
    failure_rms_threshold: float = IMS_FAILURE_RMS_G,
) -> Optional[Dict[str, Any]]:
    """
    Build a RulTrajectory from all data files in a NASA IMS dataset directory.

    Files are sorted by name (timestamp embedded in filename or alphabetical).
    Failure point = first file where RMS exceeds the threshold.
    """
    files = sorted([
        f for f in data_dir.iterdir()
        if f.is_file() and not f.name.startswith(".")
    ])

    if len(files) < 50:
        logger.warning("[NASA_IMS] Only %d files in %s — skipping.", len(files), data_dir.name)
        return None

    # First pass: compute RMS of each file to locate failure point
    rms_values: List[float] = []
    for f in files:
        data = _parse_ims_file(f)
        if data is None or data.shape[1] <= max(bearing_col_x, bearing_col_y):
            rms_values.append(0.0)
            continue
        ch_x = data[:, bearing_col_x]
        ch_y = data[:, bearing_col_y]
        feats = _compute_features_from_channels(ch_x, ch_y, rpm)
        rms_values.append(feats["rms_g"])

    rms_arr = np.array(rms_values)
    fail_indices = np.where(rms_arr >= failure_rms_threshold)[0]
    fail_index   = int(fail_indices[0]) if len(fail_indices) > 0 else len(rms_arr) - 1

    total_run_hours = (fail_index * IMS_INTERVAL_MIN) / 60.0
    logger.info(
        "[NASA_IMS] %s  files=%d  fail_idx=%d  total_hours=%.1fh",
        traj_id, len(files), fail_index, total_run_hours,
    )

    # Second pass: build trajectory points
    points: List[Dict[str, Any]] = []
    for i, f in enumerate(files[:fail_index + 1]):
        data = _parse_ims_file(f)
        if data is None or data.shape[1] <= max(bearing_col_x, bearing_col_y):
            continue
        ch_x = data[:, bearing_col_x]
        ch_y = data[:, bearing_col_y]
        feats = _compute_features_from_channels(ch_x, ch_y, rpm)

        run_hours = (i * IMS_INTERVAL_MIN) / 60.0
        rul_hours = max(0.0, total_run_hours - run_hours)

        points.append({
            "runHours":        round(run_hours, 4),
            "imuAcceleration": round(feats["rms_g"], 5),
            "tempCompressor":  35.0,       # IMS rig ambient temp ~35°C
            "emMachineLoad":   100.0,      # Fully loaded (6000 lbs applied)
            "rpm":             rpm,
            "trueRulHours":    round(rul_hours, 4),
        })

    if len(points) < 20:
        return None

    return {
        "trajectoryId": traj_id,
        "machineId":    "NASA_IMS_BENCHMARK",
        "component":    "DE_bearing",
        "assetClass":   IMS_BEARING_KEY,
        "points":       points,
    }


# ---------------------------------------------------------------------------
# Main ingestion runner
# ---------------------------------------------------------------------------

def run(
    data_root: Optional[Path] = None,
    use_kaggle_api: bool = False,
) -> Dict[str, Any]:
    """
    Ingest the NASA IMS dataset.

    The dataset is NOT freely downloadable without registration.
    This function supports two modes:
    1. local_dir mode (preferred): user has manually downloaded and unzipped
       the dataset to data/training/cache/nasa_ims/.
    2. kaggle_api mode: uses the Kaggle Python API (requires kaggle.json credentials).

    Directory structure expected (local_dir mode):
        data/training/cache/nasa_ims/
            1st_test/   — Set 1 (35M rev test)
            2nd_test/   — Set 2 (bearing 1 outer-race failure)
            3rd_test/   — Set 3 (bearing 3 outer-race failure)

    Args:
        data_root:      Root of the nasa_ims cache directory.
        use_kaggle_api: If True, attempt to download via Kaggle API.

    Returns:
        Summary dict with trajectory counts.
    """
    from src.mlops.store import add_trajectory
    from src.schemas.training import RulTrajectory, TrajectoryPoint

    if data_root is None:
        data_root = Path(__file__).resolve().parents[4] / "data" / "training" / "cache" / "nasa_ims"
    data_root.mkdir(parents=True, exist_ok=True)

    # Try Kaggle API download if requested and data is missing
    set2_dir = data_root / "2nd_test"
    set3_dir = data_root / "3rd_test"

    if use_kaggle_api and not set2_dir.exists():
        logger.info("[NASA_IMS] Attempting Kaggle API download …")
        _try_kaggle_download(data_root)

    # Identify which sets are available
    datasets_to_ingest = []
    if set2_dir.exists() and any(set2_dir.iterdir()):
        # Set 2: Bearing 1 outer-race failure → channel 0 (B1-X) and 1 (B1-Y)
        datasets_to_ingest.append(("IMS_Set2_B1_BPFO", set2_dir, 0, 1))
    if set3_dir.exists() and any(set3_dir.iterdir()):
        # Set 3: Bearing 3 outer-race failure → channel 4 (B3-X) and 5 (B3-Y)
        datasets_to_ingest.append(("IMS_Set3_B3_BPFO", set3_dir, 4, 5))

    if not datasets_to_ingest:
        return {
            "dataset":      "NASA_IMS",
            "status":       "data_not_found",
            "trajectories": 0,
            "hint": (
                "Manually download the NASA IMS dataset:\n"
                "  1. Register at: https://ti.arc.nasa.gov/tech/dash/groups/pcoe/prognostic-data-repository/\n"
                "  2. Download 'IMS Bearing Dataset' (Set 2 and Set 3)\n"
                f"  3. Unzip into: {data_root}/2nd_test/ and {data_root}/3rd_test/\n"
                "  OR: pip install kaggle && configure ~/.kaggle/kaggle.json,\n"
                "  then call run(use_kaggle_api=True)"
            ),
        }

    total_trajs  = 0
    total_points = 0

    for traj_id, data_dir, col_x, col_y in datasets_to_ingest:
        logger.info("[NASA_IMS] Processing %s from %s …", traj_id, data_dir.name)
        traj_data = _build_trajectory_from_dir(data_dir, traj_id, col_x, col_y)
        if traj_data is None:
            continue
        try:
            points = [TrajectoryPoint(**p) for p in traj_data["points"]]
            traj = RulTrajectory(
                trajectoryId=traj_data["trajectoryId"],
                machineId=traj_data["machineId"],
                component=traj_data["component"],
                assetClass=traj_data["assetClass"],
                points=points,
            )
            add_trajectory(traj)
            total_trajs  += 1
            total_points += len(points)
            logger.info("[NASA_IMS] ✓ %s — %d points written.", traj_id, len(points))
        except Exception as exc:
            logger.warning("[NASA_IMS] Failed to write %s: %s", traj_id, exc)

    summary = {
        "dataset":        "NASA_IMS",
        "status":         "ok" if total_trajs > 0 else "no_trajectories_written",
        "trajectories":   total_trajs,
        "total_points":   total_points,
        "data_root":      str(data_root),
        "ready_for_pinn": total_trajs >= 2,
    }
    logger.info("[NASA_IMS] Ingestion complete: %s", summary)
    return summary


def _try_kaggle_download(target_dir: Path) -> None:
    """
    Attempt to download NASA IMS dataset via Kaggle Python API.
    Requires: pip install kaggle && ~/.kaggle/kaggle.json credentials.
    Dataset: vinayak123tyagi/bearing-dataset (community upload of IMS data).
    """
    try:
        import kaggle  # type: ignore
        logger.info("[NASA_IMS] Downloading via Kaggle API …")
        kaggle.api.authenticate()
        kaggle.api.dataset_download_files(
            "vinayak123tyagi/bearing-dataset",
            path=str(target_dir),
            unzip=True,
            quiet=False,
        )
        logger.info("[NASA_IMS] Kaggle download complete → %s", target_dir)
    except ImportError:
        logger.warning(
            "[NASA_IMS] kaggle package not installed. "
            "Run: pip install kaggle  and configure ~/.kaggle/kaggle.json"
        )
    except Exception as exc:
        logger.warning("[NASA_IMS] Kaggle download failed: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    result = run(use_kaggle_api=False)
    print("\n=== NASA IMS Ingestion Summary ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
