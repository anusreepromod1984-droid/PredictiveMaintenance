"""IMU-Acceleration RMS vs X/Y/Z spectrum prediction modes."""

from unittest.mock import MagicMock

from src.agents.agent_gamma import AgentGamma
from src.schemas.predictions import CableCheckStatus
from src.schemas.telemetry import HarmonicPeak, TelemetryFrame
from src.services.mqtt_service import MQTTIngestionService


def _electrical(**overrides):
    payload = dict(
        machineId="compressor_unit_01",
        tempMotor=27.4,
        tempAmbient=27.6,
        emIr=0.0,
        emIy=0.0,
        emIb=0.0,
        emVr=388.0,
        emVy=394.0,
        emVb=389.0,
        emMachineLoad=0.0,
        emVoltageImbalance=1.07,
        emPower=0.0,
        emFrequency=50.0,
    )
    payload.update(overrides)
    return payload


def test_imu_acceleration_only_is_iso_zone_and_rul_not_named_defect():
    frame = TelemetryFrame(**_electrical(
        imuAcceleration=0.1351,
        sourceKeys=["imuAcceleration", "tempMotor", "emIr", "emVr", "emPower"],
    ))
    assert frame.vibration_prediction_mode() == "imu_rms"

    gamma = AgentGamma()
    cable = CableCheckStatus(status="VALID", alert_suppressed=False, pattern_recognition_status="STEADY_CLIMB")
    defect, rul = gamma.process(frame, cable_check=cable)
    assert defect.prediction_mode == "imu_rms"
    assert defect.diagnosis_method == "iso20816_rms_only"
    assert defect.defect_code == "NORMAL"
    assert defect.defect_code not in {"BPFI", "MF001", "MF002", "MF003"}
    assert rul.rul_days > 0


def test_imu_rms_high_is_unclassified_not_imbalance():
    frame = TelemetryFrame(**_electrical(
        imuAcceleration=5.2,
        sourceKeys=["imuAcceleration", "tempMotor", "emIr"],
    ))
    defect, _rul = AgentGamma().process(frame)
    assert defect.prediction_mode == "imu_rms"
    assert defect.defect_code == "ANOMALY_UNCLASSIFIED"
    assert "Zone C" in defect.defect_name
    assert defect.diagnosis_method == "iso20816_rms_only"


def test_xyz_scalars_without_spectrum_stay_on_rms_path():
    svc = MQTTIngestionService(orchestrator=MagicMock())
    frame = svc._parse_payload_to_frame(
        {"parameters": [
            {"name": "X-Axis vibration", "parameters": [0.8]},
            {"name": "Y-Axis vibration", "parameters": [0.6]},
            {"name": "Z-Axis vibration", "parameters": [1.1]},
        ]},
        machine_id="compressor_unit_01",
    )
    assert frame is not None
    assert frame.has_triaxial_axes()
    assert frame.vibration_prediction_mode() == "triaxial_rms"
    assert frame.imu_acceleration == pytest_approx_rms(0.8, 0.6, 1.1)
    defect, _rul = AgentGamma().process(frame)
    assert defect.prediction_mode == "triaxial_rms"
    assert defect.diagnosis_method == "iso20816_rms_only"


def pytest_approx_rms(x, y, z):
    import math
    return math.sqrt(x * x + y * y + z * z)


def test_xyz_plus_harmonics_unlocks_named_defect_path():
    frame = TelemetryFrame(**_electrical(
        imuAcceleration=6.2,
        rpm=2950,
        xAxisVibration=3.1,
        yAxisVibration=2.8,
        zAxisVibration=4.9,
        vibrationHarmonics=[
            HarmonicPeak(frequency=49.2, amplitude=1.2),
            HarmonicPeak(frequency=98.4, amplitude=6.1),
        ],
    ))
    assert frame.vibration_prediction_mode() == "triaxial_spectrum"
    defect, _rul = AgentGamma().process(frame)
    assert defect.prediction_mode == "triaxial_spectrum"
    assert defect.diagnosis_method != "iso20816_rms_only"


def test_mqtt_imu_name_sets_imu_rms_mode():
    svc = MQTTIngestionService(orchestrator=MagicMock())
    frame = svc._parse_payload_to_frame(
        {"parameters": [
            {"name": "IMU-Acceleration", "unit": "mm/sec", "parameters": [0.1351]},
            {"name": "NTC Temperature sensor x 2", "parameters": [27.37, 27.56]},
        ]},
        machine_id="compressor_unit_01",
    )
    assert frame is not None
    assert frame.imu_acceleration == 0.1351
    assert frame.vibration_prediction_mode() == "imu_rms"
    assert frame.waveform is None


def test_live_integrated_data_without_imu_does_not_halt():
    """Current plant snapshot: electricity + NTC + humidity + pressure, no IMU."""
    from src.agents.orchestrator import APMSOrchestrator

    payload = {"parameters": [
        {"name": "Energymeter - Ir", "parameters": [0.0]},
        {"name": "Energymeter - Iy", "parameters": [0.0]},
        {"name": "Energymeter - Ib", "parameters": [0.0]},
        {"name": "Energymeter - Vr", "parameters": [391.6]},
        {"name": "Energymeter - Vy", "parameters": [397.13]},
        {"name": "Energymeter - Vb", "parameters": [391.76]},
        {"name": "Energymeter - Voltage Imbalance", "parameters": [0.925]},
        {"name": "Energymeter - Power", "parameters": [0.0]},
        {"name": "Energymeter - Machine load", "parameters": [0.0]},
        {"name": "Humidity", "parameters": [50.25]},
        {"name": "NTC Temperature sensor x 2", "parameters": [28.5, 28.62]},
        {"name": "Pressure sensor x 2 [SW]", "parameters": [3.792]},
        {"name": "Microphone - Sound level", "parameters": [-0.87]},
    ]}
    svc = MQTTIngestionService(orchestrator=MagicMock())
    svc._imu_seed_attempted.add("compressor_unit_01")
    frame = svc._parse_payload_to_frame(payload, machine_id="compressor_unit_01")
    assert frame is not None
    assert frame.field_was_sent("imuAcceleration") is False
    assert frame.field_was_sent("tempMotor") is True
    assert frame.humidity == 50.25
    assert frame.pressure == 3.792
    assert frame.vibration_prediction_mode() == "absent"

    response = APMSOrchestrator().run(frame)
    assert response.cable_check.status == "VALID"
    assert response.overall_health_status != "SENSOR_FAULT"
    assert response.iso_vibration_zone is None
    assert response.defect_localization is not None
    assert response.defect_localization.prediction_mode == "absent"
    assert response.defect_localization.diagnosis_method == "imu_absent"


def test_integrated_data_after_vibration_uses_held_imu_rms():
    from src.agents.orchestrator import APMSOrchestrator

    svc = MQTTIngestionService(orchestrator=MagicMock())
    svc._parse_payload_to_frame(
        {"parameters": [{"name": "IMU-Acceleration", "unit": "mm/sec", "parameters": [0.1351]}]},
        machine_id="compressor_unit_01",
    )
    payload = {"parameters": [
        {"name": "Energymeter - Ir", "parameters": [0.0]},
        {"name": "Energymeter - Power", "parameters": [0.0]},
        {"name": "Energymeter - Energy", "parameters": [39.54]},
        {"name": "Humidity", "parameters": [44.36]},
        {"name": "NTC Temperature sensor x 2", "parameters": [65.69, 53.13]},
        {"name": "Pressure sensor x 2 [SW]", "parameters": [6.823]},
        {"name": "Microphone - Sound level", "parameters": [3.82]},
    ]}
    frame = svc._parse_payload_to_frame(payload, machine_id="compressor_unit_01")
    assert frame.imu_acceleration == 0.1351
    assert frame.field_was_sent("imuAcceleration") is True
    assert frame.vibration_prediction_mode() == "imu_rms"

    response = APMSOrchestrator().run(frame)
    assert response.defect_localization is not None
    assert response.defect_localization.prediction_mode == "imu_rms"
    assert response.defect_localization.diagnosis_method != "imu_absent"
