"""
Configuration & Thresholds REST API Endpoints
Allows live retrieval of active operational thresholds loaded from .env
"""

from fastapi import APIRouter
from src.config import settings
from src.api.auth import public_security_status
from typing import Dict, Any

router = APIRouter(prefix="/api/v1/config", tags=["Configuration"])


@router.get("/public", summary="Unauthenticated security flags for the dashboard")
async def public_config() -> Dict[str, Any]:
    return public_security_status()


@router.get("/thresholds", summary="Get All Operational Thresholds")
async def get_thresholds() -> Dict[str, Any]:
    """
    Returns all industrial parameter thresholds (Vibration, Thermal, Acoustics,
    Orientation, Power Quality, NAMUR NE43, RUL Policy) loaded dynamically from .env.
    """
    return {
        "status": "success",
        "environment": settings.ENVIRONMENT,
        "thresholds": settings.get_thresholds_dict()
    }


@router.get("/summary", summary="Get Key Thresholds Summary")
async def get_thresholds_summary() -> Dict[str, Any]:
    """
    Returns high-level threshold summary for dashboard status badges.
    """
    return {
        "vibration_warning_mm_s": settings.VIB_WARNING_MAX,
        "vibration_danger_mm_s": settings.VIB_DANGER_MIN,
        "vibration_post_repair_mm_s": settings.VIBRATION_POST_REPAIR_MAX,
        "temp_motor_warning_c": settings.TEMP_MOTOR_WARNING_MAX,
        "temp_motor_danger_c": settings.TEMP_MOTOR_DANGER_MIN,
        "temp_compressor_warning_c": settings.TEMP_COMPRESSOR_WARNING_MAX,
        "sound_warning_db": settings.SOUND_WARNING_MAX,
        "vuf_warning_pct": settings.VUF_WARNING_MAX,
        "genuine_overheat_delta_c": settings.GENUINE_OVERHEAT_DELTA_C,
        "rul_skip_threshold_days": settings.RUL_SKIP_THRESHOLD_DAYS,
    }
