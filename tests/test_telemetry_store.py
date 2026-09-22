"""Tests for MQTT → Postgres row mapping (no live database required)."""

from datetime import datetime, timezone

from src.database.telemetry_store import frame_to_db_params, persist_mqtt_frame
from src.schemas.telemetry import TelemetryFrame
from src.config import settings


def _frame() -> TelemetryFrame:
    return TelemetryFrame(
        machineId="compressor_unit_01",
        timestamp=datetime(2026, 9, 9, 7, 0, tzinfo=timezone.utc),
        imuAcceleration=1.82,
        rpm=1480,
        tempMotor=48.5,
        tempCompressor=52.1,
        tempAmbient=25.0,
        humidity=45.0,
        emIr=14.2,
        emIy=14.1,
        emIb=14.0,
        emVr=400.0,
        emVy=400.0,
        emVb=399.0,
        emMachineLoad=78.5,
        emVoltageImbalance=1.2,
        emPower=28.4,
        runHours=1420.5,
        vibrationHarmonics=[{"frequency": 24.7, "amplitude": -22.5}],
        waveformZ=[0.1] * 256,
    )


def test_frame_to_db_params_maps_live_columns():
    params = frame_to_db_params(_frame(), mqtt_topic="pdm/integrated_json")
    assert params["machineId"] == "compressor_unit_01"
    assert params["imuAcceleration"] == 1.82
    assert params["mqttTopic"] == "pdm/integrated_json"
    assert params["sensorOk"] is True
    assert "24.7" in params["vibrationHarmonics"]
    assert "waveform" not in params
    assert "waveformZ" not in params


def test_persist_disabled_returns_false(monkeypatch):
    monkeypatch.setattr(settings, "TELEMETRY_PERSIST_ENABLED", False)
    assert persist_mqtt_frame(_frame(), mqtt_topic="pdm/integrated_json") is False


def test_persist_skips_alert_topic(monkeypatch):
    monkeypatch.setattr(settings, "TELEMETRY_PERSIST_ENABLED", True)
    assert persist_mqtt_frame(_frame(), mqtt_topic="pdm/alerts") is False


def test_agent_snapshot_params_store_inputs_without_waveforms():
    from types import SimpleNamespace
    from src.database.telemetry_store import agent_inputs_from_frame, agent_snapshot_params

    frame = _frame()
    frame.source_keys = ["imuAcceleration", "tempMotor", "emPower", "runHours"]
    response = SimpleNamespace(
        trace_id="trc_test_1",
        timestamp=frame.timestamp,
        overall_health_score=81.0,
        overall_health_status="DEGRADED",
        skip_reason=None,
        iso_vibration_zone="A",
        cable_check=SimpleNamespace(pattern_recognition_status="STABLE"),
        thermal_de_weathering=None,
        electrical_health=SimpleNamespace(isolated_failure_domain="MECHANICAL"),
        defect_localization=SimpleNamespace(
            defect_code="NORMAL",
            diagnosis_method="iso20816_rms_only",
            prediction_mode="imu_rms",
        ),
        rul_prediction=SimpleNamespace(
            rul_operating_hours=432.0,
            rul_days=18.0,
            method="physics_wear_law_plus_weibull",
        ),
        cmms_work_order=None,
    )
    inputs = agent_inputs_from_frame(frame)
    assert "waveformZ" not in inputs
    assert inputs["imuAcceleration"] == 1.82
    params = agent_snapshot_params(frame, response)
    assert params["traceId"] == "trc_test_1"
    assert params["imuAcceleration"] == 1.82
    assert params["rulOperatingHours"] == 432.0
    assert "1.82" in params["inputs"]

