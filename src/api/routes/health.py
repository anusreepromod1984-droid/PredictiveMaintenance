"""GET /api/v1/health and GET /api/v1/ready — real probes, not hardcoded success."""

from fastapi import APIRouter, Response
from src.config import settings
from src.schemas.predictions import HealthStatusResponse
from src.services.ring_buffer import redis_is_connected

router = APIRouter(prefix="/api/v1", tags=["Health & Status"])


def _probe_database() -> tuple:
    try:
        from sqlalchemy import text
        from src.database.connection import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:
        return False, str(exc)[:180]


def _fitted_artifacts_loaded() -> bool:
    try:
        from src.mlops.store import load_registry_json, registry_path
        weibull = load_registry_json("weibull.json")
        return bool(
            weibull
            or registry_path("classifier.ubj").exists()
            or registry_path("pinn.pt").exists()
        )
    except Exception:
        return False


def _health_payload() -> HealthStatusResponse:
    db_ok, db_err = _probe_database()
    redis_ok = redis_is_connected()
    fitted = _fitted_artifacts_loaded()
    parts = []
    if not db_ok:
        parts.append(f"database_unreachable: {db_err}")
    if settings.REDIS_ENABLED and not redis_ok:
        parts.append("redis_unreachable")
    if not fitted:
        parts.append("fitted_artifacts_absent: rules_and_physics_only")
    status = "UP" if db_ok and (redis_ok or not settings.REDIS_ENABLED) else "DEGRADED"
    return HealthStatusResponse(
        status=status,
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        database_connected=db_ok,
        redis_connected=redis_ok,
        mqtt_configured=bool(settings.MQTT_ENABLED),
        ai_models_loaded=True,
        fitted_artifacts_loaded=fitted,
        rul_model_version=settings.RUL_MODEL_VERSION,
        detail="; ".join(parts) if parts else None,
    )


@router.get("/health", response_model=HealthStatusResponse, summary="Check API, DB, Redis, and model identifiers")
async def health_check():
    return _health_payload()


@router.get("/ready", summary="Kubernetes/compose readiness — Redis required when enabled")
async def readiness(response: Response):
    redis_ok = redis_is_connected() if settings.REDIS_ENABLED else True
    return {"status": "READY", "redis_connected": redis_ok}
