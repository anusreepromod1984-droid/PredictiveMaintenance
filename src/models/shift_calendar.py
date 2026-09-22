"""Approved repair windows — night crew vs Saturday planned outage."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src.utils.logger import get_logger

logger = get_logger("Models.ShiftCalendar")

_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "shift_calendar.json")
_CACHE: Optional[Dict[str, Any]] = None


def load_shift_calendar() -> Dict[str, Any]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        with open(_PATH, "r", encoding="utf-8") as handle:
            _CACHE = json.load(handle)
    except Exception as exc:
        logger.error("Failed to load shift_calendar.json: %s", exc)
        _CACHE = {
            "timezone": "Asia/Kolkata",
            "night": {"start_hour": 22, "duration_hours": 8, "crew": "Night A", "kind": "night_window"},
            "planned_outage": {"weekday": 5, "start_hour": 6, "end_hour": 14, "crew": "Saturday A", "kind": "planned_outage"},
            "urgent_rul_days": 3,
        }
    return _CACHE


def plant_zone() -> ZoneInfo:
    name = str(load_shift_calendar().get("timezone") or "Asia/Kolkata")
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _as_local(now: Optional[datetime] = None) -> datetime:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return clock.astimezone(plant_zone())


def _window_dict(start: datetime, end: datetime, crew: str, kind: str) -> Dict[str, Any]:
    label = (
        f"{start.strftime('%a')} {start.day} {start.strftime('%b')} "
        f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')} · {crew}"
    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "crew": crew,
        "kind": kind,
        "label": label,
    }


def next_night_window(now: Optional[datetime] = None) -> Dict[str, Any]:
    cfg = load_shift_calendar().get("night") or {}
    start_hour = int(cfg.get("start_hour") or 22)
    duration = int(cfg.get("duration_hours") or 8)
    crew = str(cfg.get("crew") or "Night A")
    kind = str(cfg.get("kind") or "night_window")
    local = _as_local(now)
    start = local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if local >= start:
        start = start + timedelta(days=1)
    end = start + timedelta(hours=duration)
    return _window_dict(start, end, crew, kind)


def next_planned_outage(now: Optional[datetime] = None) -> Dict[str, Any]:
    cfg = load_shift_calendar().get("planned_outage") or {}
    weekday = int(cfg.get("weekday") if cfg.get("weekday") is not None else 5)
    start_hour = int(cfg.get("start_hour") or 6)
    end_hour = int(cfg.get("end_hour") or 14)
    crew = str(cfg.get("crew") or "Saturday A")
    kind = str(cfg.get("kind") or "planned_outage")
    local = _as_local(now)
    days_ahead = (weekday - local.weekday()) % 7
    candidate = (local + timedelta(days=days_ahead)).replace(
        hour=start_hour, minute=0, second=0, microsecond=0
    )
    if days_ahead == 0 and local.hour >= end_hour:
        candidate = candidate + timedelta(days=7)
    end = candidate.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    return _window_dict(candidate, end, crew, kind)


def next_repair_window(
    rul_days: float,
    ticket_type: str = "PDM_CORRECTIVE",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Urgent remaining life uses tonight's crew; otherwise the next Saturday outage."""
    urgent_limit = float(load_shift_calendar().get("urgent_rul_days") or 3)
    urgent = float(rul_days) <= urgent_limit or ticket_type == "PM_CONDITION" and float(rul_days) <= 5
    if urgent:
        return next_night_window(now)
    return next_planned_outage(now)


def upcoming_windows(now: Optional[datetime] = None, weeks: int = 4) -> List[Dict[str, Any]]:
    """Next night + Saturday slots for the calendar page."""
    local = _as_local(now)
    rows: List[Dict[str, Any]] = []
    cursor = local
    for _ in range(max(1, weeks) * 2):
        night = next_night_window(cursor)
        planned = next_planned_outage(cursor)
        for item in (night, planned):
            start = datetime.fromisoformat(item["start"])
            if all(datetime.fromisoformat(existing["start"]) != start for existing in rows):
                rows.append(item)
        cursor = cursor + timedelta(days=1)
        if len(rows) >= weeks * 2:
            break
    rows.sort(key=lambda item: item["start"])
    return rows[: weeks * 2]
