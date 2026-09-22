from src.models.fault_bom import resolve_bom
from src.models.supplier_sourcing_engine import SupplierSourcingEngine


def test_each_known_error_has_the_right_part():
    cases = {
        "PF001": "AIR-LEAK-KIT",
        "BPFI": "SKF-6208",
        "BPFO": "SKF-6208",
        "MF001": "FOUNDATION-BOLT-KIT",
        "MF002": "COUPLING-L100",
        "MF003": None,
        "EF001": None,
        "SENSOR": None,
    }
    for code, key in cases.items():
        bom = resolve_bom(code, "ignored", "SKF-6208")
        assert bom.part_key == key, code


def test_unknown_error_does_not_become_a_bearing():
    bom = resolve_bom("ZZ999", "mystery", "SKF-6208")
    assert bom.part_key is None
    assert "bearing" not in bom.part_label.lower()


def test_unclassified_vibration_does_not_buy_a_bearing():
    bom = resolve_bom("ANOMALY_UNCLASSIFIED", "Mechanical Drive Assembly / Bearings", "SKF-6208")
    assert bom.part_key is None
    assert "6208" not in bom.part_label


def test_every_known_error_has_an_operator_prompt_and_spare_rule():
    from src.models.fault_bom import FAULT_BOM, OPERATOR_PROMPT, prompt_for_fault

    for code in FAULT_BOM:
        assert code in OPERATOR_PROMPT, code
        prompt = prompt_for_fault(code, "ignored", "SKF-6208")
        assert prompt["whatIsIt"]
        assert prompt["sparePart"]
        assert prompt["defect_code"] == code
        if code in {"BPFI", "BPFO"}:
            assert prompt["part_key"] == "SKF-6208"
        elif code == "PF001":
            assert prompt["part_key"] == "AIR-LEAK-KIT"
            assert "bearing" in prompt["whatIsIt"].lower()
        elif code == "MF002":
            assert prompt["part_key"] == "COUPLING-L100"
        elif code in {"EF001", "SENSOR", "HARDWARE_CABLE_FAULT", "MF003", "NORMAL", "ANOMALY_UNCLASSIFIED"}:
            assert prompt["part_key"] is None
            assert "6208" not in prompt["sparePart"]

    unknown = prompt_for_fault("ZZ999", "mystery part", "SKF-6208")
    assert unknown["part_key"] is None
    assert "6208" not in unknown["sparePart"]
    assert "ZZ999" in unknown["whatIsIt"]
    assert "invent" in unknown["sparePart"].lower() or "invent" in unknown["notThis"].lower()


def test_named_unknown_code_does_not_guess_from_leak_text():
    bom = resolve_bom("GEAR_MESH", "Compressed-air circuit / fittings", "SKF-6208")
    assert bom.part_key is None
    assert bom.defect_code == "GEAR_MESH"


def test_unknown_code_does_not_buy_the_default_bearing():
    intel = SupplierSourcingEngine().evaluate_sourcing(
        "SKF-6208",
        rul_days=10,
        defect_code="ZZ999",
    )
    assert intel.sourcing_recommendation == "NO_BOM_SPARE"
    assert intel.purchase_options == []
    assert "invent" in (intel.oem_part_number or "").lower()


def test_leak_text_without_code_still_picks_fitting_kit():
    bom = resolve_bom(None, "Compressed-air circuit / fittings / drain traps", "SKF-6208")
    assert bom.part_key == "AIR-LEAK-KIT"


def test_voltage_sourcing_skips_bearing_buy_list():
    intel = SupplierSourcingEngine().evaluate_sourcing(
        "Electric Motor Stator Winding / Power Supply",
        rul_days=20,
        defect_code="EF001",
    )
    assert intel.sourcing_recommendation == "NO_BOM_SPARE"
    assert intel.purchase_options == []
