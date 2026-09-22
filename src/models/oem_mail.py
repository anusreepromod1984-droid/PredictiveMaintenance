"""Agent Delta composes the OEM inventory email that closes the PdM loop."""

from __future__ import annotations

from typing import Any, Optional

from src.models.fleet_registry import asset_by_id
from src.schemas.predictions import (
    CMMSWorkOrder,
    DefectLocalization,
    OemMailDraft,
    RULPrediction,
)


def machine_display_name(machine_id: str) -> str:
    row = asset_by_id(machine_id)
    if row and row.get("name"):
        return str(row["name"])
    return machine_id.replace("_", " ").title()


def compose_oem_mail(
    machine_id: str,
    defect: DefectLocalization,
    rul: RULPrediction,
    wo: CMMSWorkOrder,
    *,
    sent: bool = False,
    sent_at: Optional[str] = None,
    dispatch_attempted: bool = False,
) -> OemMailDraft:
    intel = wo.sourcing_intelligence
    part = _catalog_part((intel.oem_part_number if intel else None) or wo.reserved_spare_part_bom)
    qty = int(intel.stores_qty) if intel else 0
    bin_id = (intel.stores_bin if intel else None) or None
    in_stores = qty > 0
    due = wo.pm_due_date or rul.recommended_repair_by_date
    window = wo.scheduled_repair_window or "next approved crew window"
    machine = machine_display_name(machine_id)
    issue = f"{defect.defect_name} ({defect.defect_code})"
    options = list(intel.purchase_options) if intel else []
    detail = (defect.reasoning_summary or "").strip()

    if in_stores:
        stock_bit = f"in {bin_id}" if bin_id else "in warehouse"
        subject = (
            f"{defect.defect_code} {defect.defect_name} — {part or 'spare'} {stock_bit} "
            f"· required by {due} · {machine}"
        )
    elif part:
        subject = (
            f"{defect.defect_code} {defect.defect_name} — {part} required by {due} "
            f"(not in warehouse) · {machine}"
        )
    else:
        subject = (
            f"{defect.defect_code} {defect.defect_name} on {machine} — no spare to order "
            f"· required by {due}"
        )

    part_line = part or "No mechanical spare to order for this code"
    rul_line = f"{rul.rul_days:.1f} days remaining useful life" if rul.rul_days is not None else "RUL not stated"
    ticket = str(wo.work_order_id)
    ticket_type = str(wo.ticket_type or "PDM")

    issue_block = f"{issue} on {defect.failing_component}."
    if detail and detail.lower() not in issue_block.lower():
        issue_block = f"{issue_block} {detail}"

    lines = [
        "Hi,",
        "",
        f"Welcome. APMS Agent Delta has completed the inventory check for {machine}. Please find the issue, part required, due date, and warehouse status below.",
        "",
        "Issue",
        issue_block,
        "",
        "Part required",
        part_line,
        "",
        "Required by",
        f"{due} · {window} · {rul_line}",
        "",
        "Warehouse",
        _warehouse_text(in_stores, qty, bin_id),
        "",
        "Where to get it",
        _buy_text(in_stores, options),
        "",
        f"Work order {ticket} ({ticket_type}).",
        "",
        "Thank you.",
        "APMS 4-agent pipeline",
    ]
    message = "\n".join(lines)

    if in_stores:
        preview = f"{part or 'Spare'} is in the warehouse" + (f" ({bin_id}, qty {qty})" if bin_id else f" (qty {qty})") + f". Needed by {due}."
    elif part:
        n = len(options)
        preview = f"{part} is not in the warehouse. Needed by {due}." + (f" {n} buy listing(s) attached." if n else "")
    else:
        preview = f"No spare to order. Needed by {due}."

    return OemMailDraft(
        subject=subject[:240],
        message=message,
        preview=preview,
        warehouse_in_stock=in_stores,
        required_by=str(due) if due else None,
        part_number=part,
        sent=sent,
        sent_at=sent_at,
        dispatch_attempted=dispatch_attempted,
    )


def compose_oem_mail_from_record(record: dict[str, Any]) -> OemMailDraft:
    """Rebuild the draft from a stored work order when the live DAG has not refreshed it yet."""
    prior = record.get("oem_mail") if isinstance(record.get("oem_mail"), dict) else {}
    machine_id = str(record.get("machine_id") or "")
    code = str(record.get("defect_code") or "UNKNOWN")
    intel = record.get("sourcing_intelligence") if isinstance(record.get("sourcing_intelligence"), dict) else {}
    due = record.get("pm_due_date") or (prior.get("required_by") if prior else None) or "next window"
    defect = DefectLocalization(
        defect_code=code,
        defect_name=str(record.get("defect_name") or code),
        failing_component=str(intel.get("oem_part_number") or record.get("reserved_spare_part_bom") or code),
        confidence_percentage=90.0,
    )
    rul = RULPrediction(
        rul_operating_hours=48.0,
        rul_days=2.0,
        confidence_interval_bounds="B10/B50",
        recommended_repair_by_date=str(due),
    )
    wo = CMMSWorkOrder.model_validate({
        **{k: v for k, v in record.items() if k not in {"oem_mail", "status", "registered_at", "machine_id", "defect_code", "defect_name"}},
        "work_order_id": record.get("work_order_id") or f"ADVISORY-{machine_id}",
        "scheduled_repair_window": record.get("scheduled_repair_window") or "next approved crew window",
        "reserved_spare_part_bom": record.get("reserved_spare_part_bom") or intel.get("oem_part_number") or "spare",
        "reserved_warehouse_bin": record.get("reserved_warehouse_bin") or intel.get("stores_bin") or "UNCONFIRMED_ERP",
    })
    return compose_oem_mail(
        machine_id,
        defect,
        rul,
        wo,
        sent=bool(prior.get("sent")),
        sent_at=prior.get("sent_at"),
        dispatch_attempted=bool(prior.get("dispatch_attempted") or prior.get("sent")),
    )


def _catalog_part(raw: Optional[str]) -> Optional[str]:
    """BOM label 'None' is not a spare. Do not treat it as something to order."""
    if not raw:
        return None
    text = str(raw).strip()
    low = text.lower()
    if not text or low in {"none", "null", "n/a", "-", "no spare"}:
        return None
    if low.startswith(("no spare", "no mechanical", "no machine", "no off-the-shelf", "inspect first")):
        return None
    return text


def _warehouse_text(in_stores: bool, qty: int, bin_id: Optional[str]) -> str:
    if in_stores:
        loc = bin_id or "stores"
        return f"In warehouse — {qty} piece{'s' if qty != 1 else ''} in {loc}. Pick from stores; no purchase needed."
    loc = bin_id or "the assigned bin"
    return f"Not in warehouse — {loc} is empty. Order from the listings below."


def _buy_text(in_stores: bool, options: list[Any]) -> str:
    if in_stores:
        return "No marketplace listings attached. Pick the spare from the warehouse bin named above."
    if not options:
        return "No verified buy listings for this code. Repair the diagnosed source — do not order a bearing by default."
    rows: list[str] = []
    for index, opt in enumerate(options, start=1):
        name = getattr(opt, "name", "") or ""
        finds = getattr(opt, "what_it_finds", "") or ""
        url = getattr(opt, "url", "") or ""
        quote = getattr(opt, "quote_inr", None)
        price = f"  ₹{int(quote):,}" if quote is not None else ""
        label = f"{index}. {name}{price}"
        if finds:
            label += f" — {finds}"
        rows.append(label)
        if url:
            rows.append(f"   {url}")
    return "\n".join(rows)
