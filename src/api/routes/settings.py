"""
Settings Router — stores mutable runtime preferences (e.g. alert language)
that external systems (frontend, API clients) can update without redeploying.
Persisted in Redis with an in-memory fallback.
"""
from __future__ import annotations

import threading
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel
import redis
from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("APMS.Settings")

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

_lock = threading.Lock()
_state: dict = {
    "alert_language": "en",   # BCP-47 code: "en", "hi", "ta", "de", …
}

REDIS_KEY = "pdm:alert_language"


def _get_redis() -> Optional[redis.Redis]:
    try:
        return redis.from_url(settings.get_redis_url(), decode_responses=True, socket_connect_timeout=1)
    except Exception as e:
        logger.debug("[Settings] Redis connection error: %s", e)
        return None


def get_alert_language() -> str:
    """Return the currently configured alert notification language."""
    # Try Redis first
    r = _get_redis()
    if r:
        try:
            val = r.get(REDIS_KEY)
            if val:
                return str(val).strip()
        except Exception as e:
            logger.debug("[Settings] Redis get error: %s", e)

    # Fallback to in-process memory
    with _lock:
        return _state.get("alert_language", "en")


# ── Schemas ───────────────────────────────────────────────────────────────────
class AlertLanguagePayload(BaseModel):
    language: str  # BCP-47 code e.g. "en", "hi", "ta", "de"


class AlertLanguageResponse(BaseModel):
    alert_language: str
    message: str


# ── Routes ────────────────────────────────────────────────────────────────────
@router.get("/alert-language", response_model=AlertLanguageResponse, summary="Get alert language")
def get_alert_language_route() -> AlertLanguageResponse:
    """Returns the active alert notification language code."""
    lang = get_alert_language()
    return AlertLanguageResponse(alert_language=lang, message="OK")


@router.post("/alert-language", response_model=AlertLanguageResponse, summary="Set alert language")
def set_alert_language(payload: AlertLanguagePayload) -> AlertLanguageResponse:
    """
    Set the language for WhatsApp / Email alert notifications.

    Accepts any BCP-47 code supported by ALERT_STRINGS in alert_dispatcher.py
    (en, de, fr, es, it, pt, nl, pl, hi, ta, te, bn, mr, gu).
    Falls back to English for unrecognised codes.
    """
    code = payload.language.lower().split("-")[0].strip()   # normalise "hi-IN" → "hi"
    if not code:
        code = "en"

    # Store in memory
    with _lock:
        _state["alert_language"] = code

    # Store in Redis
    r = _get_redis()
    if r:
        try:
            r.set(REDIS_KEY, code)
            logger.info("[Settings] Saved alert_language='%s' to Redis (%s)", code, REDIS_KEY)
        except Exception as e:
            logger.warning("[Settings] Failed to save alert_language to Redis: %s", e)

    return AlertLanguageResponse(
        alert_language=code,
        message=f"Alert language set to '{code}'",
    )
