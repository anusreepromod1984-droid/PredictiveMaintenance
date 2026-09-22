"""Pull Gbotz persisted history into public.telemetry_reading, then optionally fit Isolation Forest."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.config import settings
from src.database.telemetry_store import persist_mqtt_frame
from src.mlops.fit_isolation_forest import fit_isolation_forest
from src.services.gbotz_client import (
    GbotzClientError,
    api_key_configured,
    fetch_history_rows,
    gbotz_row_to_frame,
    get_capabilities,
)
from src.utils.logger import get_logger

logger = get_logger("MLops.GbotzBackfill")


def backfill_gbotz_history(
    duration: str = "14d",
    max_points: int = 300,
    machine_ids: Optional[List[str]] = None,
    fit_after: bool = True,
    experimental: bool = True,
) -> Dict[str, Any]:
    if not settings.GBOTZ_BACKFILL_WRITE_DB:
        logger.info("[Gbotz backfill] skipped — GBOTZ_BACKFILL_WRITE_DB is false (no API call, no DB write)")
        return {
            "ok": False,
            "written": False,
            "reason": (
                "GBOTZ_BACKFILL_WRITE_DB is false. Gbotz is not called and "
                "nothing is written to Postgres."
            ),
        }
    if not api_key_configured():
        return {
            "ok": False,
            "reason": "AI_SERVICE_API_KEY is empty. Paste the Gbotz key in .env and restart.",
        }

    try:
        caps = get_capabilities()
    except GbotzClientError as exc:
        return {"ok": False, "reason": str(exc)}

    scope = (caps.get("scope") or {}).get("machineIds") or []
    ids = machine_ids or (scope if scope else None)
    limits = caps.get("limits") or {}
    per_machine = int(limits.get("maxHistoryPointsPerMachine") or max_points)
    max_points = min(max_points, per_machine)

    try:
        raw_rows = fetch_history_rows(duration=duration, max_points=max_points, machine_ids=ids)
    except GbotzClientError as exc:
        return {"ok": False, "reason": str(exc), "capabilities": {"limits": limits, "scope": scope}}

    inserted = 0
    skipped = 0
    parse_fail = 0
    for row in raw_rows:
        frame = gbotz_row_to_frame(row)
        if frame is None:
            parse_fail += 1
            continue
        if persist_mqtt_frame(frame, mqtt_topic="gbotz_backfill"):
            inserted += 1
        else:
            skipped += 1

    result: Dict[str, Any] = {
        "ok": True,
        "fetched": len(raw_rows),
        "inserted": inserted,
        "skipped_duplicate_or_db": skipped,
        "parse_fail": parse_fail,
        "duration": duration,
        "max_points": max_points,
        "machine_ids": ids,
        "limits": limits,
    }
    logger.info(
        "[Gbotz backfill] fetched=%s inserted=%s skipped=%s parse_fail=%s",
        len(raw_rows), inserted, skipped, parse_fail,
    )
    if fit_after:
        result["baseline"] = fit_isolation_forest(experimental=experimental)
    return result
