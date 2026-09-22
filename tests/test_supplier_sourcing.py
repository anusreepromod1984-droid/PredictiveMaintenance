from src.agents.agent_delta import AgentDelta
from src.agents.open_wo_tracker import OpenWorkOrderTracker
from src.models.supplier_sourcing_engine import SupplierSourcingEngine
from src.schemas.predictions import CableCheckStatus, DefectLocalization, RULPrediction
from src.schemas.telemetry import TelemetryFrame


def test_stores_reserve_when_bin_has_stock():
    intel = SupplierSourcingEngine().evaluate_sourcing("SKF-6310", rul_days=20)
    assert intel.sourcing_recommendation == "RESERVE_FROM_STORES"
    assert intel.stores_qty == 3
    assert intel.stores_bin == "BIN-B07"
    assert intel.purchase_options == []
    assert intel.ladder[1].name == "Stores"
    assert intel.ladder[1].status == "pass"


def test_tagged_vendor_when_stores_empty_but_vendor_has_stock():
    intel = SupplierSourcingEngine().evaluate_sourcing("FAG-22216", rul_days=30)
    assert intel.sourcing_recommendation == "TAGGED_VENDOR_ORDER"
    assert intel.stores_qty == 0
    assert intel.tagged_vendor_name == "Bhosari Industrial Bearings"
    assert intel.tagged_vendor_qty == 6
    assert intel.purchase_options == []


def test_marketplace_when_stores_and_vendor_fail():
    intel = SupplierSourcingEngine().evaluate_sourcing("SKF-6208", rul_days=10)
    assert intel.sourcing_recommendation == "MARKETPLACE_RFQ"
    assert intel.stores_qty == 0
    assert intel.tagged_vendor_qty == 0
    names = [opt.name for opt in intel.purchase_options]
    assert "Moglix" in names
    assert "RS Components India" in names
    assert "Industrybuying" in names
    urls = [opt.url for opt in intel.purchase_options]
    assert any("msnic614qv4" in url for url in urls)
    assert any("BEA.DEE.47402467" in url for url in urls)
    assert any("6671204" in url for url in urls)
    assert all("searchTerm=" not in url and "search?q=" not in url for url in urls)
    assert intel.ladder[-1].name == "Buy"
    assert intel.ladder[-1].status == "open"


def test_air_leak_does_not_source_a_bearing():
    intel = SupplierSourcingEngine().evaluate_sourcing(
        "Compressed-air circuit / fittings / drain traps",
        rul_days=20,
    )
    assert intel.oem_part_number == "Festo QSL-1/4-8"
    assert "SKF 6208" not in (intel.oem_part_number or "")
    assert intel.sourcing_recommendation == "MARKETPLACE_RFQ"
    assert intel.ladder[0].status == "pass"
    assert intel.ladder[1].name == "Stores"
    assert intel.ladder[1].status == "out"
    assert intel.ladder[2].name == "Vendor"
    assert intel.ladder[2].status == "out"
    assert intel.ladder[3].status == "open"
    names = [opt.name for opt in intel.purchase_options]
    urls = [opt.url for opt in intel.purchase_options]
    assert "Moglix" in names
    assert "SKF Bearings India" not in names
    assert "Grainger" not in names
    assert "DirectIndustry" not in names
    assert any("msn2r9dpkvxymf" in url for url in urls)
    assert any("PN.AI.AI.1565375" in url for url in urls)
    assert any("1216192" in url for url in urls)
    assert all("Air-circuit" not in url for url in urls)
    assert all("searchTerm=" not in url and "search?q=" not in url for url in urls)


def test_coupling_reserves_from_shim_bin():
    intel = SupplierSourcingEngine().evaluate_sourcing("Lovejoy L-100", rul_days=14)
    assert intel.sourcing_recommendation == "RESERVE_FROM_STORES"
    assert intel.stores_bin == "BIN-SHIM-04"


def test_delta_attaches_buy_options_for_default_bearing():
    frame = TelemetryFrame(
        machineId="compressor_unit_01",
        imuAcceleration=8.5,
        rpm=1480,
        tempMotor=74.0,
        tempAmbient=25.0,
        emIr=20.0,
        emPower=12.0,
        bearingModel="SKF-6208",
    )
    defect = DefectLocalization(
        defect_code="BPFI",
        defect_name="Inner-race bearing",
        failing_component="SKF-6208",
        confidence_percentage=90.0,
    )
    rul = RULPrediction(
        rul_operating_hours=168.0,
        rul_days=7.0,
        confidence_interval_bounds="B10/B50",
        recommended_repair_by_date="2026-09-18",
    )
    cable = CableCheckStatus(status="VALID", alert_suppressed=False)
    wo = AgentDelta(tracker=OpenWorkOrderTracker()).process(frame, defect, rul, cable_check=cable)
    assert wo is not None
    assert wo.reserved_warehouse_bin == "UNCONFIRMED_ERP"
    assert wo.sourcing_intelligence is not None
    assert wo.sourcing_intelligence.sourcing_recommendation == "MARKETPLACE_RFQ"
    options = wo.sourcing_intelligence.purchase_options
    assert len(options) >= 4
    assert all("searchTerm=" not in opt.url and "search?q=" not in opt.url for opt in options)
    assert any("6208-2rs1" in opt.url.lower() or "6208-2RS1" in opt.url for opt in options)
