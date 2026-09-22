"""POST inventory close-the-loop mail to the Splus SendMailAsync gateway."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

import httpx

from src.config import settings
from src.schemas.predictions import OemMailDraft
from src.utils.logger import get_logger

logger = get_logger("Services.OemMail")

_dispatch_lock = threading.Lock()
# Every defect already mailed for this machine. Same error must not mail again
# after a live MQTT tick or after switching away and back. Reset clears it.
_mailed_faults: Dict[str, Set[str]] = {}
# Kept so older tests/patches that still mention the last-code map do not break.
_last_fault_mailed: Dict[str, str] = {}

_NO_MAIL_CODES = {
    "",
    "NORMAL",
    "NONE",
    "SENSOR",
    "HARDWARE_CABLE_FAULT",
    "ANOMALY_UNCLASSIFIED",
    "EF001",
}

_NO_PART_PREFIXES = (
    "no spare",
    "no mechanical",
    "no machine",
    "no off-the-shelf",
    "inspect first",
    "none required",
)

_redis_client = None
_redis_tried = False


class OemMailError(RuntimeError):
    pass


def mail_configured() -> bool:
    return bool((settings.OEM_MAIL_TO or "").strip() and (settings.OEM_MAIL_API_URL or "").strip())


def _norm_code(defect_code: str) -> str:
    return (defect_code or "").strip().upper()


def _is_mailable_part(part: Optional[str]) -> bool:
    if not part:
        return False
    text = str(part).strip()
    low = text.lower()
    if not text or low in {"none", "null", "n/a", "-", "no spare"}:
        return False
    if any(low.startswith(prefix) for prefix in _NO_PART_PREFIXES):
        return False
    return True


def _is_mailable_fault(defect_code: str) -> bool:
    return _norm_code(defect_code) not in _NO_MAIL_CODES


def _redis():
    global _redis_client, _redis_tried
    if _redis_tried:
        return _redis_client
    _redis_tried = True
    if not settings.REDIS_ENABLED:
        return None
    try:
        import redis

        client = redis.Redis.from_url(
            settings.get_redis_url(),
            decode_responses=True,
            socket_timeout=1.5,
        )
        client.ping()
        _redis_client = client
    except Exception as exc:
        logger.warning("[OemMail] Redis unavailable for mail lock (%s). Using process memory.", exc)
        _redis_client = None
    return _redis_client


def _redis_key(machine_id: str) -> str:
    return f"{settings.REDIS_KEY_PREFIX}:oem_mailed:{machine_id}"


def _already_mailed(machine_id: str, defect_code: str) -> bool:
    code = _norm_code(defect_code)
    if code in _mailed_faults.get(machine_id, set()):
        return True
    if _last_fault_mailed.get(machine_id) == code:
        return True
    client = _redis()
    if not client:
        return False
    try:
        return bool(client.sismember(_redis_key(machine_id), code))
    except Exception as exc:
        logger.warning("[OemMail] Redis sismember failed: %s", exc)
        return False


def _mark_mailed(machine_id: str, defect_code: str) -> None:
    code = _norm_code(defect_code)
    _mailed_faults.setdefault(machine_id, set()).add(code)
    _last_fault_mailed[machine_id] = code
    client = _redis()
    if not client:
        return
    try:
        client.sadd(_redis_key(machine_id), code)
    except Exception as exc:
        logger.warning("[OemMail] Redis sadd failed: %s", exc)


def note_machine_cleared(machine_id: str) -> None:
    """Call only on an explicit machine reset — not on a healthy MQTT tick."""
    _mailed_faults.pop(machine_id, None)
    _last_fault_mailed.pop(machine_id, None)
    client = _redis()
    if not client:
        return
    try:
        client.delete(_redis_key(machine_id))
    except Exception as exc:
        logger.warning("[OemMail] Redis delete failed: %s", exc)


def send_oem_mail(draft: OemMailDraft) -> Dict[str, Any]:
    to_addr = (settings.OEM_MAIL_TO or "").strip()
    if not to_addr:
        raise OemMailError("OEM_MAIL_TO is empty in .env")
    url = (settings.OEM_MAIL_API_URL or "").strip()
    if not url:
        raise OemMailError("OEM_MAIL_API_URL is empty in .env")

    payload = {
        "From": settings.OEM_MAIL_FROM or "",
        "Pwd": settings.OEM_MAIL_PWD or "",
        "To": to_addr,
        "CC": (settings.OEM_MAIL_CC or "").strip(),
        "Bcc": (settings.OEM_MAIL_BCC or "").strip(),
        "Subject": draft.subject,
        "Message": draft.message,
        "SpClientId": (settings.OEM_MAIL_SP_CLIENT_ID or "").strip(),
    }
    logger.info(
        "[OemMail] Sending subject=%r to=%s cc=%s",
        draft.subject,
        to_addr,
        payload["CC"] or "(none)",
    )
    with httpx.Client(timeout=settings.OEM_MAIL_TIMEOUT_S) as client:
        response = client.post(url, json=payload, headers={"Content-Type": "application/json"})
    if response.status_code >= 400:
        raise OemMailError(f"SendMailAsync {response.status_code}: {response.text[:400]}")
    body: Any
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:500]}
    return {"status_code": response.status_code, "body": body, "to": to_addr, "cc": payload["CC"]}


def dispatch_if_needed(
    draft: OemMailDraft,
    *,
    machine_id: str,
    defect_code: str,
    prior: Optional[Dict[str, Any]] = None,
) -> OemMailDraft:
    """One OEM mail per error on a machine — not on every telemetry refresh.

    Sends when this machine has a spare-required fault we have not already
    mailed. Live ticks and repeated Run Diagnosis of the same code are skipped.
    Switching to a different unmailed fault (leak → bearing) sends again.
    Returning to a fault we already mailed does not.
    """
    prior = prior or {}
    if prior.get("sent"):
        draft.sent = True
        draft.sent_at = prior.get("sent_at") or draft.sent_at
        draft.dispatch_attempted = True
        _mark_mailed(machine_id, defect_code)
        return draft

    if not _is_mailable_fault(defect_code):
        return draft
    if not _is_mailable_part(draft.part_number):
        return draft
    if not mail_configured():
        return draft

    with _dispatch_lock:
        if _already_mailed(machine_id, defect_code):
            draft.sent = True
            draft.dispatch_attempted = True
            return draft
        # Claim before the HTTP post so Diagnose + MQTT cannot both send.
        _mark_mailed(machine_id, defect_code)
        try:
            send_oem_mail(draft)
            draft.sent = True
            draft.sent_at = datetime.now(timezone.utc).isoformat()
            draft.dispatch_attempted = True
            logger.info("[OemMail] Sent inventory mail for %s %s", machine_id, defect_code)
        except OemMailError as exc:
            logger.error("[OemMail] Auto-send failed: %s", exc)
            draft.dispatch_attempted = True
            # Keep the claim so a retry storm from MQTT does not double-send
            # after a gateway 200 that we failed to parse. Reset clears it.
    return draft
