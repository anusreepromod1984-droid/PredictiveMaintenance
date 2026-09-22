from src.models.fault_reasoner import fuse_live_evidence
from src.schemas.predictions import ElectricalHealth
from src.schemas.telemetry import HarmonicPeak, TelemetryFrame


def _frame(**overrides) -> TelemetryFrame:
    payload = dict(
        machineId="compressor_unit_01",
        imuAcceleration=5.2,
        rpm=1480,
        tempMotor=40.0,
        tempAmbient=25.0,
        emIr=12.0,
        emPower=8.0,
        emVoltageImbalance=1.07,
        pressure=6.8,
        soundLevel=4.0,
        sourceKeys=["imuAcceleration", "tempMotor", "emIr", "emPower", "pressure", "soundLevel"],
    )
    payload.update(overrides)
    return TelemetryFrame(**payload)


def test_high_rms_alone_stays_unclassified_not_a_bearing():
    classified = {
        "defect_code": "ANOMALY_UNCLASSIFIED",
        "defect_name": "ISO 20816 Zone C — elevated RMS 5.20 mm/s (no spectrum to localize)",
        "failing_component": "Mechanical Drive Assembly / Bearings",
        "confidence_percentage": 72.0,
        "diagnosis_method": "iso20816_rms_only",
    }
    out = fuse_live_evidence(_frame(), classified)
    assert out["defect_code"] == "ANOMALY_UNCLASSIFIED"
    assert out["diagnosis_method"] == "iso20816_rms_only"
    assert out["reasoned_part_key"] is None
    assert out["reasoning_summary"]


def test_unclassified_plus_leak_promotes_fitting_kit():
    classified = {
        "defect_code": "ANOMALY_UNCLASSIFIED",
        "defect_name": "ISO 20816 Zone C",
        "failing_component": "Mechanical Drive Assembly / Bearings",
        "confidence_percentage": 72.0,
        "diagnosis_method": "iso20816_rms_only",
    }
    frame = _frame(motorFaults=[{"fault_code": "PF001", "active": True, "confidence": 0.92, "description": "Air leak"}])
    out = fuse_live_evidence(frame, classified)
    assert out["defect_code"] == "PF001"
    assert out["reasoned_part_key"] == "AIR-LEAK-KIT"


def test_unclassified_plus_vuf_promotes_electrical_no_bearing():
    classified = {
        "defect_code": "ANOMALY_UNCLASSIFIED",
        "defect_name": "ISO 20816 Zone C",
        "failing_component": "Mechanical Drive Assembly / Bearings",
        "confidence_percentage": 72.0,
        "diagnosis_method": "iso20816_rms_only",
    }
    elec = ElectricalHealth(
        voltage_unbalance_pct=4.8,
        machine_load_pct=80.0,
        stator_winding_status="Phase Imbalance Warning",
        isolated_failure_domain="ELECTRICAL",
    )
    out = fuse_live_evidence(_frame(emVoltageImbalance=4.8), classified, domain="ELECTRICAL", electrical=elec)
    assert out["defect_code"] == "EF001"
    assert out["reasoned_part_key"] is None


def test_unclassified_plus_2x_harmonic_promotes_coupling():
    classified = {
        "defect_code": "ANOMALY_UNCLASSIFIED",
        "defect_name": "ISO 20816 Zone C",
        "failing_component": "Mechanical Drive Assembly / Bearings",
        "confidence_percentage": 72.0,
        "diagnosis_method": "iso20816_rms_only",
    }
    shaft = 1480 / 60.0
    frame = _frame(
        imuAcceleration=6.2,
        vibrationHarmonics=[
            HarmonicPeak(frequency=round(shaft, 1), amplitude=-20.0),
            HarmonicPeak(frequency=round(2 * shaft, 1), amplitude=-2.0),
        ],
    )
    out = fuse_live_evidence(frame, classified)
    assert out["defect_code"] == "MF002"
    assert out["reasoned_part_key"] == "COUPLING-L100"
