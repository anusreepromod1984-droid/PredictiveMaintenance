"""Greenbotz demo SAP PM / CRM — local tickets, bins, and vendors. Not a live SAP system."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger("Models.DemoCrm")

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
STORES_PATH = os.path.join(DATA, "plant_stores.json")
CATALOG_PATH = os.path.join(DATA, "supplier_interchangeability_catalog.json")

from src.models.shift_calendar import next_repair_window

MACHINE_LABELS = {
    "compressor_unit_01": "Compressor Unit 01 — Elson EL30 Reciprocating Piston (720 RPM, 3HP)",
}


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.error(f"Failed to load {path}: {exc}")
        return {}


def load_plant() -> Dict[str, Any]:
    return _load_json(STORES_PATH)


def is_demo_crm_connected(stores: Optional[Dict[str, Any]] = None) -> bool:
    plant = (stores or load_plant()).get("plant") or {}
    return bool(plant.get("crm_connected"))


def demo_sap_order_id(machine_id: str, defect_code: str) -> str:
    """Stable dummy SAP PM order (4xxxxxxx). Same machine+defect always maps to the same number."""
    digest = hashlib.sha256(f"{machine_id}:{defect_code}".encode("utf-8")).hexdigest()
    n = int(digest[:6], 16) % 900000 + 100000
    return f"4{n:06d}"


def _part_label(catalog: Dict[str, Any], part_key: str) -> str:
    info = (catalog.get("parts_database") or {}).get(part_key) or {}
    oem = info.get("oem_details") or {}
    return str(oem.get("part_number") or part_key)


def _with_calendar(ticket: Dict[str, Any], rul_days: float, ticket_type: str) -> Dict[str, Any]:
    slot = next_repair_window(rul_days, ticket_type)
    ticket["pm_due_date"] = str(slot["start"])[:10]
    ticket["repair_window"] = slot["label"]
    ticket["repair_crew"] = slot["crew"]
    ticket["ticket_type"] = ticket_type
    return ticket


def _seed_tickets(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    machine = "compressor_unit_01"
    return [
        _with_calendar({
            "work_order_id": demo_sap_order_id(machine, "PF001"),
            "machine_id": machine,
            "machine_name": MACHINE_LABELS[machine],
            "defect_code": "PF001",
            "title": "Compressed-air leak on discharge circuit",
            "status": "REL",
            "priority": "2",
            "part_key": "AIR-LEAK-KIT",
            "part": _part_label(catalog, "AIR-LEAK-KIT"),
            "bin": "BIN-AIR-02",
            "sourcing": "MARKETPLACE_RFQ",
            "source": "seed",
        }, 2.0, "PM_CONDITION"),
        _with_calendar({
            "work_order_id": demo_sap_order_id(machine, "MF002"),
            "machine_id": machine,
            "machine_name": MACHINE_LABELS[machine],
            "defect_code": "MF002",
            "title": "2× misalignment — coupling spider + shims",
            "status": "REL",
            "priority": "3",
            "part_key": "COUPLING-L100",
            "part": _part_label(catalog, "COUPLING-L100"),
            "bin": "BIN-SHIM-04",
            "sourcing": "RESERVE_FROM_STORES",
            "source": "seed",
        }, 14.0, "PDM_CORRECTIVE"),
        _with_calendar({
            "work_order_id": demo_sap_order_id(machine, "BPFI"),
            "machine_id": machine,
            "machine_name": MACHINE_LABELS[machine],
            "defect_code": "BPFI",
            "title": "Inner-race bearing (drive end)",
            "status": "CRTD",
            "priority": "1",
            "part_key": "SKF-6208",
            "part": _part_label(catalog, "SKF-6208"),
            "bin": "BIN-A12",
            "sourcing": "MARKETPLACE_RFQ",
            "source": "seed",
        }, 4.0, "PDM_CORRECTIVE"),
    ]


def _ticket_from_open_wo(row: Dict[str, Any], catalog: Dict[str, Any]) -> Dict[str, Any]:
    machine_id = str(row.get("machine_id") or "")
    defect = str(row.get("defect_code") or "")
    intel = row.get("sourcing_intelligence") or {}
    part = intel.get("oem_part_number") or row.get("reserved_spare_part_bom") or ""
    return {
        "work_order_id": row.get("work_order_id") or demo_sap_order_id(machine_id, defect),
        "machine_id": machine_id,
        "machine_name": MACHINE_LABELS.get(machine_id, machine_id),
        "defect_code": defect,
        "title": str(row.get("reserved_spare_part_bom") or defect),
        "status": "REL",
        "priority": "2",
        "part_key": "",
        "part": part,
        "bin": row.get("reserved_warehouse_bin") or intel.get("stores_bin") or "",
        "sourcing": intel.get("sourcing_recommendation") or "",
        "source": "live",
        "pm_due_date": row.get("pm_due_date"),
        "repair_window": row.get("scheduled_repair_window"),
        "repair_crew": row.get("repair_crew"),
        "ticket_type": row.get("ticket_type"),
    }


def build_demo_crm() -> Dict[str, Any]:
    stores = load_plant()
    catalog = _load_json(CATALOG_PATH)
    plant = stores.get("plant") or {}
    warehouse = stores.get("warehouse") or {}
    vendors_by_part = stores.get("tagged_vendors") or {}

    bins: List[Dict[str, Any]] = []
    for key, row in warehouse.items():
        qty = int(row.get("qty") or 0)
        min_qty = int(row.get("min_qty") or 0)
        bins.append({
            "part_key": key,
            "part": _part_label(catalog, key),
            "bin": row.get("bin"),
            "qty": qty,
            "min_qty": min_qty,
            "available": qty > 0,
            "below_min": qty < min_qty,
        })
    bins.sort(key=lambda item: (not item["available"], item["part_key"]))

    vendors: List[Dict[str, Any]] = []
    for key, rows in vendors_by_part.items():
        for vendor in rows or []:
            qty = int(vendor.get("qty_on_hand") or 0)
            vendors.append({
                "part_key": key,
                "part": _part_label(catalog, key),
                "name": vendor.get("name"),
                "city": vendor.get("city"),
                "phone": vendor.get("phone"),
                "oem_line": vendor.get("oem_line"),
                "qty_on_hand": qty,
                "lead_time_days": vendor.get("lead_time_days"),
                "unit_inr": vendor.get("unit_inr"),
                "available": qty > 0,
            })

    tickets: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    try:
        from src.agents.open_wo_tracker import wo_tracker
        for row in wo_tracker.list_open_work_orders():
            ticket = _ticket_from_open_wo(row, catalog)
            key = (str(ticket["machine_id"]), str(ticket["defect_code"]))
            if key in seen:
                continue
            seen.add(key)
            tickets.append(ticket)
    except Exception as exc:
        logger.warning(f"Could not list live open WOs: {exc}")

    for seed in _seed_tickets(catalog):
        key = (seed["machine_id"], seed["defect_code"])
        if key in seen:
            continue
        seen.add(key)
        tickets.append(seed)

    return {
        "demo": True,
        "notice": "Greenbotz Demo SAP PM — not a live SAP system. Tickets, bins, and vendors are plant-file data for ladder tests.",
        "plant": {
            "name": plant.get("name", "Factory Floor 1"),
            "city": plant.get("city", ""),
            "pin": plant.get("pin", ""),
            "crm_system": plant.get("crm_system", "Greenbotz Demo SAP PM"),
            "crm_connected": bool(plant.get("crm_connected")),
            "sap_client": plant.get("sap_client", "100"),
            "sap_plant": plant.get("sap_plant", "1000"),
            "planner_group": plant.get("planner_group", "PM1"),
            "planner_name": plant.get("planner_name", "Planner"),
        },
        "tickets": tickets,
        "stores": bins,
        "vendors": vendors,
    }
