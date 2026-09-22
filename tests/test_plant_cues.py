from unittest.mock import MagicMock

from src.agents.agent_beta import AgentBeta
from src.agents.agent_gamma import AgentGamma
from src.models.iso_20816 import evaluation_velocity_rms
from src.models.plant_cues import process_leak_overlay
from src.schemas.predictions import CableCheckStatus
from src.schemas.telemetry import TelemetryFrame
from src.services.mqtt_service import MQTTIngestionService


def _running(**overrides) -> TelemetryFrame:
    payload = dict(
        machineId="compressor_unit_01",
        imuAcceleration=0.13,
        tempMotor=65.7,
        tempCompressor=53.1,
        emIr=0.0,
        emIy=0.0,
        emIb=0.0,
        emVr=396.0,
        emVy=402.0,
        emVb=396.0,
        emMachineLoad=0.0,
        emVoltageImbalance=1.06,
        emPower=0.0,
        emPowerFactor=1.0,
        emEnergy=39.54,
        emThdVr=2.0,
        emThdVy=2.0,
        emThdVb=2.0,
        emFrequency=50.1,
        pressure=6.82,
        soundLevel=3.82,
        micHarmonics=[
            {"frequency": 906.2, "amplitude": -3.69},
            {"frequency": 1109.4, "amplitude": -11.04},
        ],
        motorFaults=[],
        sourceKeys=[
            "imuAcceleration", "tempMotor", "tempCompressor", "emIr", "emPower", "emPowerFactor",
            "emEnergy", "emThdVr", "emFrequency", "pressure", "soundLevel", "micHarmonics",
        ],
    )
    payload.update(overrides)
    return TelemetryFrame(**payload)


def test_imu_three_value_array_is_xyz_not_first_sample_rms():
    svc = MQTTIngestionService(orchestrator=MagicMock())
    frame = svc._parse_payload_to_frame(
        {"parameters": [{"name": "IMU-Acceleration", "unit": "mm/sec", "parameters": [0.11, 0.19, 0.14]}]},
        machine_id="compressor_unit_01",
    )
    assert frame.x_axis_vibration == 0.11
    assert frame.y_axis_vibration == 0.19
    assert frame.z_axis_vibration == 0.14
    assert frame.has_triaxial_axes() is True
    assert abs(evaluation_velocity_rms(frame) - 0.19) < 1e-9


def test_named_axis_fields_and_iso_uses_worst_axis():
    svc = MQTTIngestionService(orchestrator=MagicMock())
    frame = svc._parse_payload_to_frame(
        {"parameters": [
            {"name": "IMU-Acceleration X", "parameters": [0.10]},
            {"name": "IMU-Acceleration Y", "parameters": [0.12]},
            {"name": "IMU-Acceleration Z", "parameters": [2.6]},
        ]},
        machine_id="compressor_unit_01",
    )
    assert frame.vibration_prediction_mode() == "triaxial_rms"
    assert abs(evaluation_velocity_rms(frame) - 2.6) < 1e-9
    defect, _rul = AgentGamma().process(frame)
    assert defect.prediction_mode == "triaxial_rms"
    assert "Zone B" in defect.defect_name or defect.defect_code == "NORMAL"


def test_pf001_gateway_fault_is_iso11011_leak_not_bearing():
    frame = _running(motorFaults=[{
        "fault_code": "PF001",
        "description": "Pressure Leakage Observed!!",
        "confidence": 0.9,
        "active": True,
    }], sourceKeys=[
        "imuAcceleration", "tempMotor", "pressure", "soundLevel", "micHarmonics", "motorFaults", "emPower",
    ])
    overlay = process_leak_overlay(frame, [])
    assert overlay is not None
    assert overlay["defect_code"] == "PF001"
    defect, rul = AgentGamma().process(frame, history=[])
    assert defect.defect_code == "PF001"
    assert "iso11011" in defect.diagnosis_method
    assert rul.rul_days <= 3.0


def test_acoustic_leak_without_gateway_code():
    frame = _running()
    overlay = process_leak_overlay(frame, [])
    assert overlay is not None
    assert overlay["diagnosis_method"] == "iso22096_airborne"
    defect, _rul = AgentGamma().process(frame, history=[])
    assert defect.defect_code == "PF001"


def test_ieee519_thd_sets_electrical_warning():
    cable = CableCheckStatus(status="VALID", alert_suppressed=False, pattern_recognition_status="STABLE")
    _thermal, electrical = AgentBeta().process(_running(emThdVr=6.2, emThdVy=2.0, emThdVb=2.0), cable)
    assert electrical.stator_winding_status == "HARMONIC_DISTORTION_WARNING"
    assert electrical.thd_pct == 6.2


def test_zero_ct_low_pf_is_process_leak_not_electrical():
    cable = CableCheckStatus(status="VALID", alert_suppressed=False, pattern_recognition_status="STABLE")
    frame = _running(
        emPowerFactor=0.82,
        emVoltageImbalance=0.66,
        motorFaults=[{
            "fault_code": "PF001",
            "description": "Pressure Leakage Observed!!",
            "confidence": 0.9,
            "active": True,
        }],
        sourceKeys=[
            "imuAcceleration", "tempMotor", "tempCompressor", "emIr", "emPower", "emPowerFactor",
            "emEnergy", "emThdVr", "emFrequency", "pressure", "soundLevel", "micHarmonics", "motorFaults",
        ],
    )
    _thermal, electrical = AgentBeta().process(frame, cable)
    assert electrical.isolated_failure_domain == "MECHANICAL"
    assert electrical.stator_winding_status != "LOW_POWER_FACTOR"


def test_airborne_leak_is_mechanical_not_electrical():
    cable = CableCheckStatus(status="VALID", alert_suppressed=False, pattern_recognition_status="STABLE")
    _thermal, electrical = AgentBeta().process(_running(emPowerFactor=0.82, emVoltageImbalance=1.01), cable)
    assert electrical.isolated_failure_domain == "MECHANICAL"


def test_effective_load_uses_nameplate_kw_not_ct_percent():
    from src.models.plant_cues import effective_load_pct

    frame = _running(
        emPower=2.42,
        emMachineLoad=88.14,
        emIr=4.4,
        emIy=4.2,
        emIb=4.1,
        sourceKeys=["emPower", "emMachineLoad", "emIr", "emIy", "emIb"],
    )
    assert abs(effective_load_pct(frame) - 6.5) < 0.2
