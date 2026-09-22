"""
run_all.py — Master runner for all public benchmark dataset ingestors.
=======================================================================
Downloads and converts all available public PdM benchmark datasets into
our training inbox (data/training/inbox/), then optionally triggers
model training (XGBoost classifier + PINN RUL) if minima are met.

Datasets handled:
  1. CWRU     — Bearing fault classification (NORMAL / BPFI / BPFO) [AUTO-DOWNLOAD]
  2. PRONOSTIA— Run-to-failure trajectories for PINN training        [AUTO-DOWNLOAD]
  3. NASA IMS — Run-to-failure trajectories (heavy industrial)       [LOCAL or KAGGLE]

Usage:
    python -m src.mlops.datasets.run_all               # download + ingest only
    python -m src.mlops.datasets.run_all --train       # ingest + retrain models
    python -m src.mlops.datasets.run_all --train --exp # ingest + retrain experimental
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("MLops.RunAll")


def run_all_ingestion(use_cache: bool = True) -> Dict[str, Any]:
    """Download and ingest all auto-downloadable benchmark datasets."""
    results: Dict[str, Any] = {}

    # -----------------------------------------------------------------------
    # 1. CWRU Bearing Dataset — full auto-download
    # -----------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("STEP 1/3 — CWRU Bearing Fault Classification Dataset")
    logger.info("=" * 60)
    try:
        from src.mlops.datasets.cwru_ingestor import run as cwru_run
        results["cwru"] = cwru_run(use_cache=use_cache)
    except Exception as exc:
        logger.error("[RunAll] CWRU failed: %s", exc)
        results["cwru"] = {"status": "error", "error": str(exc)}

    # -----------------------------------------------------------------------
    # 2. PRONOSTIA Run-to-Failure Dataset — auto-download via Zenodo
    # -----------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("STEP 2/3 — PRONOSTIA (FEMTO-ST) Run-to-Failure Dataset")
    logger.info("=" * 60)
    try:
        from src.mlops.datasets.pronostia_ingestor import run as pronostia_run
        results["pronostia"] = pronostia_run(use_cache=use_cache)
    except Exception as exc:
        logger.error("[RunAll] PRONOSTIA failed: %s", exc)
        results["pronostia"] = {"status": "error", "error": str(exc)}

    # -----------------------------------------------------------------------
    # 3. NASA IMS Dataset — local directory or Kaggle API
    # -----------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("STEP 3/3 — NASA IMS Bearing Run-to-Failure Dataset")
    logger.info("=" * 60)
    try:
        from src.mlops.datasets.nasa_ims_ingestor import run as ims_run
        results["nasa_ims"] = ims_run(use_kaggle_api=False)  # local only by default
    except Exception as exc:
        logger.error("[RunAll] NASA IMS failed: %s", exc)
        results["nasa_ims"] = {"status": "error", "error": str(exc)}

    return results


def run_training(experimental: bool = False) -> Dict[str, Any]:
    """Trigger XGBoost + PINN training if minima are met after ingestion."""
    train_results: Dict[str, Any] = {}

    logger.info("=" * 60)
    logger.info("TRAINING — XGBoost Fault Classifier")
    logger.info("=" * 60)
    try:
        from src.mlops.fit_classifier import fit_classifier
        train_results["classifier"] = fit_classifier(experimental=experimental)
        status = "✓ trained" if train_results["classifier"].get("ok") else "✗ below minimum"
        logger.info("[RunAll] Classifier: %s", status)
    except Exception as exc:
        logger.error("[RunAll] Classifier training failed: %s", exc)
        train_results["classifier"] = {"ok": False, "error": str(exc)}

    logger.info("=" * 60)
    logger.info("TRAINING — PyTorch PINN (Remaining Useful Life)")
    logger.info("=" * 60)
    try:
        from src.mlops.fit_pinn import fit_pinn
        # Try the bearing classes we have data for
        for asset_class in ["NTN-6803", "SKF-6205", "REXNORD-ZA2115", "SKF-6206"]:
            result = fit_pinn(asset_class=asset_class, experimental=experimental)
            if result.get("ok"):
                train_results[f"pinn_{asset_class}"] = result
                logger.info("[RunAll] PINN trained for %s ✓", asset_class)
                break
            else:
                logger.info("[RunAll] PINN for %s: %s", asset_class, result.get("reason", "?"))
        else:
            train_results["pinn"] = {"ok": False, "reason": "No asset class met trajectory minima"}
    except Exception as exc:
        logger.error("[RunAll] PINN training failed: %s", exc)
        train_results["pinn"] = {"ok": False, "error": str(exc)}

    return train_results


def print_summary(
    ingestion: Dict[str, Any],
    training: Optional[Dict[str, Any]] = None,
) -> None:
    """Print a clean summary table to stdout."""
    print("\n" + "=" * 70)
    print("  PREDICTIVE MAINTENANCE — PUBLIC DATASET INGESTION REPORT")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    print("\n📦 Dataset Ingestion:")
    for ds, res in ingestion.items():
        status = res.get("status", "?")
        icon   = "✓" if status == "ok" else "✗"
        extra  = ""
        if "total_windows" in res:
            extra = f"  windows={res['total_windows']}  by_label={res.get('by_label', {})}"
        elif "trajectories" in res:
            extra = f"  trajectories={res['trajectories']}  points={res.get('total_points', '?')}"
        print(f"  {icon} {ds.upper():12s}  status={status}{extra}")

    if training:
        print("\n🤖 Model Training:")
        for model, res in training.items():
            ok   = res.get("ok", False)
            icon = "✓" if ok else "✗"
            if ok:
                n    = res.get("n_windows") or res.get("n_trajectories") or "?"
                ver  = res.get("version", "?")
                print(f"  {icon} {model:25s}  version={ver}  samples={n}")
            else:
                reason = res.get("reason") or res.get("error") or "?"
                print(f"  {icon} {model:25s}  {reason}")

    print("\n💡 Next steps:")
    cwru_ok = ingestion.get("cwru", {}).get("status") == "ok"
    pron_ok = ingestion.get("pronostia", {}).get("status") == "ok"
    ims_ok  = ingestion.get("nasa_ims", {}).get("status") == "ok"

    if cwru_ok:
        print("  • CWRU data loaded → run POST /api/v1/training/fit_classifier")
    else:
        print("  • CWRU: check internet connectivity and retry")
    if pron_ok:
        print("  • PRONOSTIA data loaded → run POST /api/v1/training/fit_pinn")
    else:
        print("  • PRONOSTIA: check Zenodo connectivity and retry")
    if not ims_ok:
        print("  • NASA IMS: register at https://ti.arc.nasa.gov/tech/dash/groups/pcoe/")
        print("    and place data in: data/training/cache/nasa_ims/")
    print()


# Needed for the type annotation inside print_summary
from typing import Optional


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and ingest public PdM benchmark datasets."
    )
    parser.add_argument(
        "--train", action="store_true",
        help="After ingestion, retrain XGBoost and PINN if minima are met."
    )
    parser.add_argument(
        "--exp", action="store_true",
        help="Use experimental (lower sample minima) for training."
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Force re-download even if files are cached locally."
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    ingestion = run_all_ingestion(use_cache=not args.no_cache)
    training  = run_training(experimental=args.exp) if args.train else None
    print_summary(ingestion, training)


if __name__ == "__main__":
    main()
