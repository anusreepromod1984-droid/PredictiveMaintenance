"""Plant asset roster: criticality, line, standing PM tasks."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger("Models.FleetRegistry")

_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "fleet_registry.json")
_CACHE: Optional[Dict[str, Any]] = None


def load_fleet_registry() -> Dict[str, Any]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        with open(_PATH, "r", encoding="utf-8") as handle:
            _CACHE = json.load(handle)
    except Exception as exc:
        logger.error("Failed to load fleet_registry.json: %s", exc)
        _CACHE = {"assets": []}
    return _CACHE


def list_registered_assets() -> List[Dict[str, Any]]:
    return list((load_fleet_registry().get("assets") or []))


def asset_by_id(machine_id: str) -> Optional[Dict[str, Any]]:
    for row in list_registered_assets():
        if row.get("machine_id") == machine_id:
            return row
    return None


def criticality_for(machine_id: str, default: int = 3) -> int:
    row = asset_by_id(machine_id)
    try:
        value = int((row or {}).get("criticality") or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(5, value))
