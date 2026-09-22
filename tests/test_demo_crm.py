from src.models.demo_crm import build_demo_crm, demo_sap_order_id, is_demo_crm_connected
from src.models.supplier_sourcing_engine import SupplierSourcingEngine


def test_demo_crm_board_has_mixed_stock_and_named_vendors():
    board = build_demo_crm()
    assert board["demo"] is True
    assert board["plant"]["crm_connected"] is True
    assert board["plant"]["crm_system"] == "Greenbotz Demo SAP PM"
    assert board["plant"]["planner_name"] == "K. Rao"
    assert any(t["defect_code"] == "PF001" and t["work_order_id"].startswith("4") for t in board["tickets"])
    leak = next(t for t in board["tickets"] if t["defect_code"] == "PF001")
    assert leak["repair_window"]
    assert "NEXT_APPROVED_CMMS_WINDOW" not in leak["repair_window"]
    assert leak["pm_due_date"]
    leak_bin = next(row for row in board["stores"] if row["part_key"] == "AIR-LEAK-KIT")
    shim_bin = next(row for row in board["stores"] if row["part_key"] == "COUPLING-L100")
    assert leak_bin["available"] is False
    assert shim_bin["available"] is True
    names = [v["name"] for v in board["vendors"]]
    assert "Peenya Pneumatics" in names
    assert "Peenya Precision Bearings" in names


def test_demo_sap_order_id_is_stable():
    a = demo_sap_order_id("compressor_unit_01", "PF001")
    b = demo_sap_order_id("compressor_unit_01", "PF001")
    c = demo_sap_order_id("compressor_unit_01", "BPFI")
    assert a == b
    assert a.startswith("4")
    assert len(a) == 7
    assert a != c


def test_crm_connected_marks_ladder_pass():
    assert is_demo_crm_connected() is True
    intel = SupplierSourcingEngine().evaluate_sourcing("Lovejoy L-100", rul_days=14)
    assert intel.ladder[0].name == "CRM"
    assert intel.ladder[0].status == "pass"
    assert "Greenbotz" in intel.ladder[0].detail
    assert intel.sourcing_recommendation == "RESERVE_FROM_STORES"
