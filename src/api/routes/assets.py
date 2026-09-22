"""
Assets API Route: Dynamically discovers assets from MQTT and Remote AI API.
"""

from fastapi import APIRouter
from typing import List, Dict, Any

from src.services.mqtt_service import MQTTIngestionService
from src.database.telemetry_store import list_asset_telemetry_from_db
from src.api.socketio_server import to_frontend_telemetry

router = APIRouter(prefix="/api/v1", tags=["Assets & Inventory"])

# Shared singleton reference to MQTT service
_mqtt_instance = None

def set_mqtt_service(service: MQTTIngestionService):
    global _mqtt_instance
    _mqtt_instance = service

@router.get("/assets", summary="List assets from live MQTT and remote AI API")
async def list_assets() -> Dict[str, Any]:
    """Returns assets seen on MQTT or returned by the remote AI API. No catalog placeholders."""
    if _mqtt_instance:
        assets = _mqtt_instance.get_discovered_assets()
    else:
        temp_service = MQTTIngestionService()
        assets = temp_service.get_discovered_assets()

    return {
        "count": len(assets),
        "assets": assets
    }

@router.get("/assets/{asset_id}/telemetry", summary="Get latest live telemetry for a specific asset")
async def get_asset_telemetry(asset_id: str) -> Dict[str, Any]:
    """Returns the latest operational sensor telemetry frame for the requested asset."""
    if _mqtt_instance:
        return _mqtt_instance.get_asset_telemetry(asset_id)
    temp_service = MQTTIngestionService()
    return temp_service.get_asset_telemetry(asset_id)


@router.get("/assets/{asset_id}/history", summary="Get real persisted telemetry history")
async def get_asset_history(asset_id: str, limit: int = 600) -> Dict[str, Any]:
    rows = []
    for raw in list_asset_telemetry_from_db(asset_id, limit=limit):
        mapped = to_frontend_telemetry(raw, asset_id, raw.get("timestamp"))
        if mapped:
            rows.append(mapped)
    return {"rows": rows}

