"""POST /api/v1/oem-mail/send — close the loop by emailing Agent Delta's inventory briefing."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.agents.open_wo_tracker import wo_tracker
from src.schemas.predictions import OemMailDraft
from src.services.oem_mailer import OemMailError, mail_configured, send_oem_mail
from src.utils.logger import get_logger

logger = get_logger("API.OemMail")
router = APIRouter(prefix="/api/v1", tags=["OEM Mail"])


class SendOemMailRequest(BaseModel):
    machine_id: str = Field(..., description="Asset id, e.g. compressor_unit_01")
    defect_code: Optional[str] = Field(default=None, description="Optional; defaults to the open WO on this machine")


@router.post("/oem-mail/send")
async def send_inventory_oem_mail(body: SendOemMailRequest):
    if not mail_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OEM_MAIL_TO is not set in .env",
        )
    machine_id = body.machine_id.strip()
    defect_code = (body.defect_code or "").strip() or None
    record = (
        wo_tracker.has_open_work_order(machine_id, defect_code)
        if defect_code
        else wo_tracker.latest_open_work_order(machine_id)
    )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No open work order with an inventory check for this machine.",
        )
    code = str(record.get("defect_code") or defect_code or "")
    raw_mail = record.get("oem_mail") or {}
    if not raw_mail.get("subject") or not raw_mail.get("message"):
        from src.models.oem_mail import compose_oem_mail_from_record

        draft = compose_oem_mail_from_record({**record, "machine_id": machine_id, "defect_code": code})
        record["oem_mail"] = draft.model_dump(mode="json")
        wo_tracker.register_open_work_order(machine_id, code, record)
    else:
        draft = OemMailDraft.model_validate(raw_mail)
    try:
        result = send_oem_mail(draft)
    except OemMailError as exc:
        logger.error("[OemMail] send failed for %s: %s", machine_id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    sent_at = datetime.now(timezone.utc).isoformat()
    wo_tracker.mark_oem_mail_sent(machine_id, code, sent_at)
    return {
        "ok": True,
        "to": result["to"],
        "cc": result["cc"],
        "subject": draft.subject,
        "preview": draft.preview,
        "sent_at": sent_at,
        "work_order_id": record.get("work_order_id"),
        "defect_code": code,
    }
