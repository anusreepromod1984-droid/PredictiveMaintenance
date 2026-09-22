"""
Agent Delta sourcing ladder:
1. Open a CMMS / CRM draft (advisory until a live ticket id exists).
2. Check plant stores for the part.
3. Ask tagged vendors (then other tagged vendors).
4. If stores and vendors fail — OEM locators, Indian B2B, then global MRO.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from src.schemas.predictions import PurchaseOption, SourcingStep, SupplierSourcingIntelligence
from src.models.fault_bom import resolve_bom
from src.utils.logger import get_logger

logger = get_logger("Models.SupplierSourcingEngine")

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
CATALOG_PATH = os.path.join(DATA, "supplier_interchangeability_catalog.json")
STORES_PATH = os.path.join(DATA, "plant_stores.json")
CHANNELS_PATH = os.path.join(DATA, "purchase_channels.json")


def _load_json(path: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.error(f"Failed to load {path}: {exc}")
        return fallback


class SupplierSourcingEngine:
    def __init__(
        self,
        catalog_path: str = CATALOG_PATH,
        stores_path: str = STORES_PATH,
        channels_path: str = CHANNELS_PATH,
    ):
        self.database = _load_json(catalog_path, {}).get("parts_database", {})
        self.stores = _load_json(stores_path, {})
        self.channels = _load_json(channels_path, {})

    def find_part_key(self, part_query: str, defect_code: Optional[str] = None) -> Optional[str]:
        bom = resolve_bom(defect_code, part_query, None)
        if bom.part_key:
            return bom.part_key
        # A defect code was given (known no-spare or unknown). Never guess SKF-6208 from the default model.
        if (defect_code or "").strip():
            return None
        no_spare = {"EF001", "SENSOR", "HARDWARE_CABLE_FAULT", "NORMAL", "MF003", "ANOMALY_UNCLASSIFIED"}
        if bom.defect_code in no_spare:
            return None
        raw = part_query or ""
        clean_q = raw.upper().replace(" ", "").replace("-", "")
        if not clean_q:
            return None
        for key, info in self.database.items():
            aliases = [key, info.get("oem_details", {}).get("part_number", "")]
            aliases.extend(alt.get("part_number", "") for alt in info.get("compatible_alternates", []))
            for alias in aliases:
                clean_k = str(alias).upper().replace(" ", "").replace("-", "")
                if clean_k and (clean_k in clean_q or clean_q in clean_k):
                    return key
        return None

    def evaluate_sourcing(
        self,
        component_query: str,
        rul_days: float,
        force_oem_discontinued: bool = False,
        defect_code: Optional[str] = None,
    ) -> SupplierSourcingIntelligence:
        key = self.find_part_key(component_query, defect_code=defect_code)
        if not key:
            bom = resolve_bom(defect_code, component_query, None)
            return SupplierSourcingIntelligence(
                oem_supplier_name="Not a spare-part fault",
                oem_part_number=bom.part_label,
                oem_still_in_market=False,
                oem_lead_time_days=0,
                alternate_supplier_needed=False,
                sourcing_recommendation="NO_BOM_SPARE",
                crm_status="ADVISORY_DRAFT",
                winning_source=bom.part_label,
                ladder=[
                    SourcingStep(step=1, name="CRM", status="open", detail="Draft the job"),
                    SourcingStep(step=2, name="Stores", status="skip", detail="No BOM spare"),
                    SourcingStep(step=3, name="Vendor", status="skip", detail="No vendor line"),
                    SourcingStep(step=4, name="Buy", status="skip", detail=bom.reason),
                ],
            )
        part_info = self.database.get(key, {})
        oem = part_info.get("oem_details", {})
        oem_name = oem.get("manufacturer", "SKF Bearings Ltd.")
        oem_part = oem.get("part_number", "SKF 6208-2RS1/C3")
        oem_in_market = bool(oem.get("in_market_active", True)) and not force_oem_discontinued
        oem_lead_time = int(oem.get("standard_lead_time_days", 3))
        catalog_inr = int(round(float(oem.get("unit_cost_usd", 48.5)) * 83))

        plant = self.stores.get("plant", {})
        warehouse = self.stores.get("warehouse", {})
        vendors_by_part = self.stores.get("tagged_vendors", {})
        bin_row = warehouse.get(key) or {}
        stores_qty = int(bin_row.get("qty", 0) or 0)
        stores_bin = bin_row.get("bin")
        tagged = list(vendors_by_part.get(key) or [])
        best_vendor = None
        if tagged:
            in_stock = [v for v in tagged if int(v.get("qty_on_hand", 0) or 0) > 0]
            pool = in_stock or tagged
            best_vendor = sorted(pool, key=lambda v: (int(v.get("lead_time_days", 99)), -int(v.get("qty_on_hand", 0))))[0]
        vendor_qty = int(best_vendor.get("qty_on_hand", 0) or 0) if best_vendor else 0
        vendor_lead = int(best_vendor.get("lead_time_days", 99) or 99) if best_vendor else 99
        vendor_name = best_vendor.get("name") if best_vendor else None

        ladder: List[SourcingStep] = []
        purchase_options: List[PurchaseOption] = []
        alternates = part_info.get("compatible_alternates", [])
        best_alt = None
        if alternates:
            best_alt = sorted(alternates, key=lambda x: (x.get("lead_time_days", 99), -x.get("in_stock_inventory", 0)))[0]

        crm_connected = bool(plant.get("crm_connected"))
        planner = plant.get("planner_name") or "Planner"
        crm_system = plant.get("crm_system") or "Plant CMMS / CRM"
        ladder.append(SourcingStep(
            step=1,
            name="CRM",
            status="pass" if crm_connected else "open",
            detail=f"{crm_system} · {planner}" if crm_connected else "Draft — planner accepts",
        ))

        if stores_qty > 0:
            ladder.append(SourcingStep(
                step=2, name="Stores", status="pass",
                detail=f"{stores_qty} in {stores_bin or 'bin'}",
            ))
            ladder.append(SourcingStep(step=3, name="Vendor", status="skip", detail="Not needed"))
            ladder.append(SourcingStep(step=4, name="Buy", status="skip", detail="Not needed"))
            return self._result(
                oem_name, oem_part, oem_in_market, oem_lead_time, best_alt,
                recommendation="RESERVE_FROM_STORES",
                stores_qty=stores_qty, stores_bin=stores_bin,
                vendor_name=vendor_name, vendor_qty=vendor_qty,
                winning=f"Stores {stores_bin}",
                ladder=ladder, options=[],
            )

        ladder.append(SourcingStep(
            step=2, name="Stores", status="out",
            detail=f"0 in {stores_bin}" if stores_bin else "0 on shelf",
        ))

        if best_vendor and vendor_qty > 0 and vendor_lead <= max(rul_days, 0):
            city = best_vendor.get("city") or ""
            ladder.append(SourcingStep(
                step=3, name="Vendor", status="pass",
                detail=f"{vendor_name}: {vendor_qty} pcs / {vendor_lead}d" + (f" · {city}" if city else ""),
            ))
            ladder.append(SourcingStep(step=4, name="Buy", status="skip", detail="Not needed"))
            return self._result(
                oem_name, oem_part, oem_in_market, oem_lead_time, best_alt,
                recommendation="TAGGED_VENDOR_ORDER",
                stores_qty=0, stores_bin=stores_bin,
                vendor_name=vendor_name, vendor_qty=vendor_qty,
                winning=vendor_name,
                ladder=ladder, options=[],
            )

        if best_vendor:
            ladder.append(SourcingStep(
                step=3, name="Vendor", status="out",
                detail=f"{vendor_name}: {vendor_qty} pcs" + (f" / {vendor_lead}d" if vendor_qty > 0 else " · 0 on hand"),
            ))
        else:
            ladder.append(SourcingStep(
                step=3, name="Vendor", status="out",
                detail="No tagged stockist",
            ))

        purchase_options = self._build_purchase_options(part_info, catalog_inr)
        if purchase_options:
            ladder.append(SourcingStep(
                step=4, name="Buy", status="open",
                detail=f"{len(purchase_options)} verified listings",
            ))
        else:
            ladder.append(SourcingStep(
                step=4, name="Buy", status="none",
                detail="No verified product listing",
            ))

        if oem_in_market and oem_lead_time <= rul_days and not force_oem_discontinued:
            recommendation = "MARKETPLACE_RFQ"
        elif best_alt:
            recommendation = "MARKETPLACE_RFQ"
        else:
            recommendation = "MARKETPLACE_RFQ"

        logger.info(
            f"[Supplier Engine] {key} stores={stores_qty} vendor={vendor_name or '-'} "
            f"qty={vendor_qty} → {recommendation} ({len(purchase_options)} options)"
        )
        return self._result(
            oem_name, oem_part, oem_in_market, oem_lead_time, best_alt,
            recommendation=recommendation,
            stores_qty=0, stores_bin=stores_bin,
            vendor_name=vendor_name, vendor_qty=vendor_qty,
            winning="Marketplace RFQ",
            ladder=ladder, options=purchase_options,
        )

    def _result(
        self,
        oem_name: str,
        oem_part: str,
        oem_in_market: bool,
        oem_lead_time: int,
        best_alt: Optional[Dict[str, Any]],
        recommendation: str,
        stores_qty: int,
        stores_bin: Optional[str],
        vendor_name: Optional[str],
        vendor_qty: int,
        winning: Optional[str],
        ladder: List[SourcingStep],
        options: List[PurchaseOption],
    ) -> SupplierSourcingIntelligence:
        alt_needed = recommendation not in {"RESERVE_FROM_STORES", "TAGGED_VENDOR_ORDER"}
        return SupplierSourcingIntelligence(
            oem_supplier_name=oem_name,
            oem_part_number=oem_part,
            oem_still_in_market=oem_in_market,
            oem_lead_time_days=oem_lead_time,
            alternate_supplier_needed=alt_needed and bool(best_alt),
            alternate_supplier_name=best_alt.get("manufacturer") if best_alt else None,
            compatible_part_number=best_alt.get("part_number") if best_alt else None,
            compatibility_standard=best_alt.get("compatibility_standard") if best_alt else None,
            alternate_lead_time_days=best_alt.get("lead_time_days") if best_alt else None,
            sourcing_recommendation=recommendation,
            crm_status="ADVISORY_DRAFT",
            stores_qty=stores_qty,
            stores_bin=stores_bin,
            tagged_vendor_name=vendor_name,
            tagged_vendor_qty=vendor_qty,
            winning_source=winning,
            ladder=ladder,
            purchase_options=options,
        )

    def _build_purchase_options(self, part_info: Dict[str, Any], catalog_inr: int) -> List[PurchaseOption]:
        """Only emit sites that have a verified product page for this spare.

        Generic marketplace search URLs (kit nicknames, free-text queries) are
        omitted — they land on empty results or unrelated goods.
        """
        options: List[PurchaseOption] = []
        seen_urls: set[str] = set()
        for row in part_info.get("verified_buy_links") or []:
            url = str(row.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            if any(token in url for token in ("searchTerm=", "search?q=", "/search/?q=", "searchQuery=", "search.mp?ss=")):
                logger.warning(f"Skipping unverified search URL for buy link: {url}")
                continue
            seen_urls.add(url)
            quote = row.get("quote_inr")
            options.append(PurchaseOption(
                tier=str(row.get("tier") or "india_b2b"),
                name=str(row.get("name") or ""),
                url=url,
                what_it_finds=str(row.get("what_it_finds") or ""),
                quote_inr=int(quote) if quote is not None else catalog_inr,
                lead_note=row.get("lead_note"),
                genuine_oem=bool(row.get("genuine_oem")),
            ))
        return options
