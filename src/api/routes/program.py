"""Plant program APIs: watch list and shift calendar."""

from fastapi import APIRouter

from src.models.pdm_program import build_calendar, build_watchlist

router = APIRouter(prefix="/api/v1", tags=["Plant program"])


@router.get("/fleet/watchlist")
async def get_watchlist():
    rows = build_watchlist()
    return {
        "items": rows,
        "open_count": sum(1 for row in rows if row["watch_status"] in {"severe", "warning", "watch"}),
        "severe_count": sum(1 for row in rows if row["watch_status"] == "severe"),
    }


@router.get("/calendar")
async def get_calendar():
    return build_calendar()
