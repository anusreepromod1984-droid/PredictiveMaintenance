"""
POST /api/v1/training/* — ingest plant labels and fit models when minima are met.
GET  /api/v1/training/status — what you still need to provide.
"""

from pathlib import Path
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile

from src.mlops.fit_classifier import fit_classifier
from src.mlops.fit_isolation_forest import fit_isolation_forest
from src.mlops.fit_pinn import fit_pinn
from src.mlops.fit_weibull import fit_weibull
from src.mlops.gbotz_backfill import backfill_gbotz_history
from src.mlops.status import dataset_status
from src.mlops.store import (
    add_labeled_window,
    add_life_event,
    add_trajectory,
    import_labeled_windows_csv,
    import_life_events_csv,
    import_trajectories_csv,
    templates_dir,
)
from src.schemas.training import (
    ComponentLifeEvent,
    FeedbackRequest,
    FitRequest,
    GbotzBackfillRequest,
    LabeledWindow,
    RulTrajectory,
)

router = APIRouter(prefix="/api/v1/training", tags=["Training Data & Fit"])


@router.get("/status")
async def training_status(asset_class: str = "SKF-6208"):
    return dataset_status(asset_class)


@router.post("/life-events")
async def ingest_life_events(events: List[ComponentLifeEvent]):
    for event in events:
        add_life_event(event)
    return {"ingested": len(events), "next": "GET /api/v1/training/status then POST /fit/weibull"}


@router.post("/labels")
async def ingest_labels(windows: List[LabeledWindow]):
    for window in windows:
        add_labeled_window(window)
    return {"ingested": len(windows), "next": "GET /api/v1/training/status then POST /fit/classifier"}


@router.post("/feedback")
async def technician_feedback(req: FeedbackRequest):
    window = LabeledWindow(
        machineId=req.machine_id,
        timestamp=req.timestamp or "",
        label=req.label,
        source=req.source,
        features=req.features,
        notes=req.notes,
    )
    add_labeled_window(window)
    return {"stored": True, "label": req.label}


@router.post("/trajectories")
async def ingest_trajectories(trajs: List[RulTrajectory]):
    for traj in trajs:
        add_trajectory(traj)
    return {"ingested": len(trajs), "next": "GET /api/v1/training/status then POST /fit/pinn"}


@router.post("/import/{kind}")
async def import_csv(kind: str, file: UploadFile = File(...)):
    suffix = Path(file.filename or "upload.csv").suffix.lower()
    if suffix != ".csv":
        raise HTTPException(400, "Upload a .csv matching data/training/templates/")
    dest = templates_dir().parent / "inbox" / f"upload_{kind}.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    if kind in {"life-events", "life_events", "weibull"}:
        n = import_life_events_csv(dest)
    elif kind in {"labels", "windows", "classifier"}:
        n = import_labeled_windows_csv(dest)
    elif kind in {"trajectories", "pinn"}:
        n = import_trajectories_csv(dest)
    else:
        raise HTTPException(400, "kind must be life-events | labels | trajectories")
    return {"imported": n, "kind": kind}


@router.post("/backfill/gbotz")
async def api_backfill_gbotz(req: GbotzBackfillRequest):
    result = backfill_gbotz_history(
        duration=req.duration,
        max_points=req.max_points,
        machine_ids=req.machine_ids,
        fit_after=req.fit_after,
        experimental=req.experimental,
    )
    if not result.get("ok"):
        disabled = "GBOTZ_BACKFILL_WRITE_DB" in str(result.get("reason"))
        status = 403 if disabled else (503 if "API_KEY" in str(result.get("reason")) else 400)
        raise HTTPException(status, result)
    return result


@router.post("/fit/baseline")
async def api_fit_baseline(req: FitRequest):
    result = fit_isolation_forest(experimental=req.experimental)
    if not result.get("ok"):
        raise HTTPException(400, result)
    return result


@router.post("/fit/weibull")
async def api_fit_weibull(req: FitRequest):
    result = fit_weibull(asset_class=req.asset_class, experimental=req.experimental)
    if not result.get("ok"):
        raise HTTPException(400, result)
    return result


@router.post("/fit/classifier")
async def api_fit_classifier(req: FitRequest):
    result = fit_classifier(experimental=req.experimental)
    if not result.get("ok"):
        raise HTTPException(400, result)
    return result


@router.post("/fit/pinn")
async def api_fit_pinn(req: FitRequest):
    result = fit_pinn(asset_class=req.asset_class, experimental=req.experimental)
    if not result.get("ok"):
        raise HTTPException(400, result)
    return result
