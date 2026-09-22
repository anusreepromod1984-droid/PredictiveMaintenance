"""API-key gate for /api/v1. Health and readiness stay open for load balancers."""

from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse

from src.config import settings

PUBLIC_API_PATHS = {
    "/api/v1/health",
    "/api/v1/ready",
    "/api/v1/config/public",
}


def _extract_api_key(request: Request) -> str:
    header_key = request.headers.get("x-api-key") or ""
    if header_key.strip():
        return header_key.strip()
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def auth_middleware_enabled() -> bool:
    return settings.auth_required()


async def api_key_middleware(request: Request, call_next):
    if not settings.auth_required():
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path.rstrip("/") or "/"
    if path in PUBLIC_API_PATHS or not path.startswith("/api/"):
        return await call_next(request)

    expected = (settings.APMS_API_KEY or "").strip()
    if not expected:
        return JSONResponse(
            status_code=503,
            content={"detail": "AUTH_ENABLED is true but APMS_API_KEY is not set"},
        )
    if _extract_api_key(request) != expected:
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing or invalid API key. Send X-API-Key or Authorization: Bearer."},
        )
    return await call_next(request)


def public_security_status() -> dict:
    return {
        "auth_required": settings.auth_required(),
        "api_key_header": "X-API-Key",
        "docs_enabled": settings.DOCS_ENABLED,
    }


def iter_cors_origins() -> Iterable[str]:
    return settings.cors_origin_list()
