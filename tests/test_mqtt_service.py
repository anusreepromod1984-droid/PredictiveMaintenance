"""
Unit & Integration Tests for MQTT Live Stream Ingestion Engine
"""

import json
from unittest.mock import MagicMock, patch

from src.services.mqtt_service import MQTTIngestionService
from src.schemas.telemetry import TelemetryFrame


def _sample_payload():
    return {
        "machineId": "compressor_unit_01",
        "imuAcceleration": 7.9051,
        "rpm": 1480,
        "tempMotor": 28.94,
        "tempCompressor": 29.06,
        "tempAmbient": 25.0,
        "humidity": 45.2,
        "emIr": 12.4,
        "emIy": 12.6,
        "emIb": 12.5,
        "emVr": 395.62,
        "emVy": 404.55,
        "emVb": 397.07,
        "emMachineLoad": 82.5,
        "emVoltageImbalance": 1.371,
        "emPower": 18.4,
        "soundLevel": -7.1,
        "magRoll": 0.12,
        "magPitch": 0.05,
        "magYaw": 0.01,
        "runHours": 1420.5,
        "vibrationHarmonics": [
            {"frequency": 109.4, "amplitude": -15.26}
        ]
    }


def _message(payload, topic="pdm/integrated_json"):
    mock_msg = MagicMock()
    raw = json.dumps(payload)
    mock_msg.topic = topic
    mock_msg.payload.decode.return_value = raw
    return mock_msg


@patch("src.services.mqtt_service.persist_mqtt_frame")
def test_mqtt_service_message_handling(mock_persist):
    mock_orchestrator = MagicMock()
    mqtt_service = MQTTIngestionService(orchestrator=mock_orchestrator)

    mqtt_service._on_message(None, None, _message(_sample_payload()))

    assert mock_orchestrator.run.called
    arg = mock_orchestrator.run.call_args[0][0]
    assert isinstance(arg, TelemetryFrame)
    assert arg.machine_id == "compressor_unit_01"
    assert arg.imu_acceleration == 7.9051
    mock_persist.assert_called_once()
    persisted = mock_persist.call_args[0][0]
    assert persisted.imu_acceleration == 7.9051
    assert mock_persist.call_args.kwargs["mqtt_topic"] == "pdm/integrated_json"


@patch("src.services.mqtt_service.persist_mqtt_frame")
def test_mqtt_skips_alert_topic(mock_persist):
    mock_orchestrator = MagicMock()
    mqtt_service = MQTTIngestionService(orchestrator=mock_orchestrator)
    mqtt_service._on_message(None, None, _message(_sample_payload(), topic="pdm/alerts"))
    mock_orchestrator.run.assert_not_called()
    mock_persist.assert_not_called()


@patch("src.services.mqtt_service.persist_mqtt_frame")
def test_mqtt_does_not_double_write_identical_redelivery(mock_persist):
    mock_orchestrator = MagicMock()
    mqtt_service = MQTTIngestionService(orchestrator=mock_orchestrator)
    msg = _message(_sample_payload())
    mqtt_service._on_message(None, None, msg)
    mqtt_service._on_message(None, None, msg)
    assert mock_orchestrator.run.call_count == 2
    assert mock_persist.call_count == 1


def test_subscribe_topics_are_unique():
    mqtt_service = MQTTIngestionService(orchestrator=MagicMock())
    topics = mqtt_service._subscribe_topics()
    assert len(topics) == len(set(topics))
    assert "pdm/#" not in topics
    assert "pdm/integrated_json" in topics


def test_mqtt_client_ids_are_unique():
    a = MQTTIngestionService(orchestrator=MagicMock())
    b = MQTTIngestionService(orchestrator=MagicMock())
    assert a._client_id != b._client_id
    assert a._client_id.startswith("apms-")
    assert " " not in a._client_id


def test_mqtt_stop_disconnects_client():
    mock_orchestrator = MagicMock()
    mqtt_service = MQTTIngestionService(orchestrator=mock_orchestrator)
    mqtt_service.client = MagicMock()
    mqtt_service.stop()
    mqtt_service.client.loop_stop.assert_called_once()
    mqtt_service.client.disconnect.assert_called_once()


@patch("src.database.telemetry_store.list_latest_assets_from_db", return_value=[])
@patch("urllib.request.urlopen", side_effect=OSError("offline"))
def test_discovered_assets_only_live_mqtt(_mock_urlopen, _mock_db):
    mqtt_service = MQTTIngestionService(orchestrator=MagicMock())
    assert mqtt_service.get_discovered_assets() == []
    mqtt_service.discovered_assets["compressor_unit_01"] = {
        "id": "compressor_unit_01",
        "name": "Compressor Unit 01 (Rotary Screw)",
        "status": "ONLINE",
        "source": "mqtt_live",
    }
    assets = mqtt_service.get_discovered_assets()
    assert {a["id"] for a in assets} == {"compressor_unit_01"}


class _FakeApiResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({
            "machines": [{"machineId": "line_motor_04", "meta": {"name": "Line Motor 04"}}]
        }).encode()


@patch("src.database.telemetry_store.list_latest_assets_from_db", return_value=[])
@patch("urllib.request.urlopen", return_value=_FakeApiResponse())
def test_discovered_assets_includes_api_machines_only_when_backfill_on(_mock_urlopen, _mock_db):
    from src.services import mqtt_service as mqtt_mod

    mqtt_service = MQTTIngestionService(orchestrator=MagicMock())
    with patch.object(mqtt_mod.settings, "AI_SERVICE_API_KEY", "test-key"):
        with patch.object(mqtt_mod.settings, "GBOTZ_BACKFILL_WRITE_DB", False):
            assert mqtt_service.get_discovered_assets() == []
        with patch.object(mqtt_mod.settings, "GBOTZ_BACKFILL_WRITE_DB", True):
            ids = {a["id"] for a in mqtt_service.get_discovered_assets()}
    assert ids == {"line_motor_04"}


@patch("urllib.request.urlopen", side_effect=OSError("offline"))
def test_identify_asset_uses_payload_machine_id(_mock_urlopen):
    mqtt_service = MQTTIngestionService(orchestrator=MagicMock())
    mid, name, mtype = mqtt_service._identify_asset(
        "pdm/integrated_json",
        {"machineId": "motor_drive_02"},
    )
    assert mid == "motor_drive_02"
    assert "Motor" in name
    assert mtype == "Electric Motor"


@patch("src.services.mqtt_service.persist_mqtt_frame")
def test_nested_frames_do_not_reuse_the_previous_timestamp(_mock_persist):
    """A carried-forward timestamp froze the clock on packet one: later rows were
    skipped as duplicates and the dashboard aged into 'offline' while data flowed."""
    nested = {"parameters": [
        {"name": "Energymeter - Power", "unit": "kW", "data": "float", "parameters": [8.2]},
        {"name": "NTC Temperature sensor x 2", "unit": "C", "data": "float", "parameters": [31.6]},
    ]}
    svc = MQTTIngestionService(orchestrator=MagicMock())
    first = svc._parse_payload_to_frame(nested, machine_id="compressor_unit_01")
    svc.latest_telemetry["compressor_unit_01"] = first.model_dump(by_alias=True)
    second = svc._parse_payload_to_frame(nested, machine_id="compressor_unit_01")
    assert second.timestamp > first.timestamp


@patch("src.services.mqtt_service.persist_mqtt_frame")
def test_absent_sensors_stay_flagged_on_every_packet(_mock_persist):
    """Vibration is a Product A primary. Motor RPM is derived from it, not a required MQTT field."""
    nested = {"parameters": [
        {"name": "NTC Temperature sensor x 2", "unit": "C", "data": "float", "parameters": [31.6]},
        {"name": "Energymeter - Ir", "unit": "A", "data": "float", "parameters": [0.0]},
    ]}
    svc = MQTTIngestionService(orchestrator=MagicMock())
    first = svc._parse_payload_to_frame(nested, machine_id="compressor_unit_01")
    svc.latest_telemetry["compressor_unit_01"] = first.model_dump(by_alias=True)
    second = svc._parse_payload_to_frame(nested, machine_id="compressor_unit_01")
    for frame in (first, second):
        assert "imuAcceleration" in frame.missing_blocks
        assert frame.field_was_sent("imuAcceleration") is False
        assert frame.field_was_sent("tempMotor") is True


def test_product_abc_gateway_names_map_without_fake_defaults():
    svc = MQTTIngestionService(orchestrator=MagicMock())
    fields = svc._parse_fragment({"parameters": [
        {"name": "Energymeter - Ir", "parameters": [12.1]},
        {"name": "Energymeter - Energy", "parameters": [4321.5]},
        {"name": "Energymeter - Average Power Factor", "parameters": [0.91]},
        {"name": "Energymeter - %THD Vy", "parameters": [2.3]},
        {"name": "Energymeter - Frequency", "parameters": [50.02]},
        {"name": "NTC Temperature sensor x 2", "parameters": [41.2, 27.5]},
        {"name": "Microphone - Sound level", "parameters": [64.0]},
        {"name": "Microphone - Frequency 1", "parameters": [1200.0]},
        {"name": "Microphone - Amplitude 1", "parameters": [-12.0]},
        {"name": "Pressure sensor x 2 [SW]", "parameters": [6.4, 6.3]},
        {"name": "Humidity", "parameters": [45.0]},
        {"name": "Motor faults", "parameters": [{"fault_code": "MF001", "confidence": 0.8}]},
    ]})
    assert fields["emIr"] == 12.1
    assert fields["emEnergy"] == 4321.5
    assert fields["emPowerFactor"] == 0.91
    assert fields["emThdVy"] == 2.3
    assert fields["emFrequency"] == 50.02
    assert fields["tempMotor"] == 41.2
    assert fields["tempAmbient"] == 27.5
    assert fields["tempCompressor"] == 27.5
    assert fields["soundLevel"] == 64.0
    assert fields["micHarmonics"] == [{"frequency": 1200.0, "amplitude": -12.0}]
    assert fields["pressure"] == 6.4
    assert fields["humidity"] == 45.0
    assert fields["motorFaults"][0]["fault_code"] == "MF001"
    assert fields["motorFaults"][0]["active"] is True
    zero_faults = svc._parse_fragment({"parameters": [
        {"name": "Motor faults", "parameters": [{"fault_code": "MF001", "confidence": 0.0}]},
    ]})
    assert zero_faults["motorFaults"][0]["active"] is False
    assert "imuAcceleration" not in fields
    assert "rpm" not in fields


def test_imu_acceleration_gateway_name_is_vibration_level():
    """IMU-Acceleration is the published 3-axis RMS (vibration level)."""
    svc = MQTTIngestionService(orchestrator=MagicMock())
    fields = svc._parse_fragment({"parameters": [
        {"name": "IMU-Acceleration", "unit": "mm/sec", "data": "float", "parameters": [0.1351]},
        {"name": "Humidity", "parameters": [51.22]},
        {"name": "Pressure sensor x 2 [SW]", "parameters": [3.781]},
    ]})
    assert fields["imuAcceleration"] == 0.1351
    assert fields["humidity"] == 51.22
    assert fields["pressure"] == 3.781
    frame = svc._parse_payload_to_frame(
        {"parameters": [{"name": "IMU-Acceleration", "parameters": [0.1351]}]},
        machine_id="compressor_unit_01",
    )
    assert frame.imu_acceleration == 0.1351
    assert frame.field_was_sent("imuAcceleration") is True


def test_implausible_imu_velocity_is_dropped_not_cached():
    svc = MQTTIngestionService(orchestrator=MagicMock())
    good = svc._parse_payload_to_frame(
        {"parameters": [{"name": "IMU-Acceleration", "unit": "mm/sec", "parameters": [0.1351]}]},
        machine_id="compressor_unit_01",
    )
    spiked = svc._parse_payload_to_frame(
        {"parameters": [{"name": "IMU-Acceleration", "unit": "mm/sec", "parameters": [1125.4]}]},
        machine_id="compressor_unit_01",
    )
    clipped = svc._parse_payload_to_frame(
        {"parameters": [{"name": "IMU-Acceleration", "unit": "mm/sec", "parameters": [1875.0]}]},
        machine_id="compressor_unit_01",
    )
    assert good.imu_acceleration == 0.1351
    assert spiked.imu_acceleration == 0.1351
    assert clipped.imu_acceleration == 0.1351
    later = svc._parse_payload_to_frame(
        {"parameters": [{"name": "Humidity", "parameters": [44.0]}]},
        machine_id="compressor_unit_01",
    )
    assert later.imu_acceleration == 0.1351
    assert later.field_was_sent("imuAcceleration") is True


def test_imu_capture_array_is_not_used_as_rms():
    svc = MQTTIngestionService(orchestrator=MagicMock())
    svc._parse_payload_to_frame(
        {"parameters": [{"name": "IMU-Acceleration", "parameters": [0.14]}]},
        machine_id="compressor_unit_01",
    )
    frame = svc._parse_payload_to_frame(
        {"parameters": [{"name": "IMU-Acceleration", "parameters": [1875.0, 1120.0, 900.0, 800.0, 700.0, 600.0, 500.0, 400.0]}]},
        machine_id="compressor_unit_01",
    )
    assert frame.imu_acceleration == 0.14
    assert frame.waveform is not None
    assert len(frame.waveform) == 8


def test_product_fragments_merge_only_observed_fields():
    svc = MQTTIngestionService(orchestrator=MagicMock())
    svc._imu_seed_attempted.add("compressor_unit_01")
    temp = {"parameters": [{"name": "NTC Temperature sensor x 2", "parameters": [42.0, 28.0]}]}
    electrical = {"parameters": [
        {"name": "Energymeter - Ir", "parameters": [10.0]},
        {"name": "Energymeter - Power", "parameters": [7.5]},
    ]}
    svc._parse_payload_to_frame(temp, machine_id="compressor_unit_01")
    frame = svc._parse_payload_to_frame(electrical, machine_id="compressor_unit_01")
    assert frame.temp_motor == 42.0
    assert frame.em_ir == 10.0
    assert frame.em_power == 7.5
    assert frame.field_was_sent("tempMotor")
    assert frame.field_was_sent("emPower")
    assert not frame.field_was_sent("imuAcceleration")


def test_product_a_xyz_orientation_and_product_c_pressure_humidity():
    """Table contract: Product A XYZ/orientation and Product C pressure+humidity arrive;
    environment temperature, dust, and Product C RPM do not."""
    svc = MQTTIngestionService(orchestrator=MagicMock())
    vibration = [
        {"name": "X-Axis vibration", "parameters": [0.8]},
        {"name": "Y-Axis vibration", "parameters": [0.6]},
        {"name": "Z-Axis vibration", "parameters": [1.1]},
        {"name": "Vibration level", "parameters": [1.5]},
        {"name": "Motor RPM", "parameters": [1482]},
        {"name": "Runtime", "parameters": [210.5]},
    ]
    orientation = [
        {"name": "Roll", "parameters": [0.12]},
        {"name": "Pitch", "parameters": [0.04]},
        {"name": "Yaw", "parameters": [0.01]},
    ]
    pressure = [
        {"name": "Relative pressure", "parameters": [6.4]},
        {"name": "Humidity", "parameters": [47.0]},
        {"name": "Leakage", "parameters": [0.02]},
    ]
    skipped = svc._parse_fragment({"parameters": [
        {"name": "Environment Temperature", "parameters": [28.0]},
        {"name": "Environment dust", "parameters": [12.0]},
        {"name": "Machine RPM", "parameters": [9999]},
    ]})
    svc._parse_payload_to_frame(vibration, machine_id="compressor_unit_01")
    svc._parse_payload_to_frame(orientation, machine_id="compressor_unit_01")
    frame = svc._parse_payload_to_frame(pressure, machine_id="compressor_unit_01")
    assert frame.imu_acceleration == 1.5
    assert frame.x_axis_vibration == 0.8
    assert frame.has_triaxial_axes()
    assert frame.vibration_prediction_mode() == "triaxial_rms"
    assert frame.rpm == 1482
    assert frame.run_hours == 210.5
    assert frame.mag_roll == 0.12
    assert frame.pressure == 6.4
    assert frame.humidity == 47.0
    assert frame.field_was_sent("imuAcceleration")
    assert frame.field_was_sent("humidity")
    assert "dust" not in skipped
    assert "tempAmbient" not in skipped
    assert skipped.get("rpm") != 9999


@patch("urllib.request.urlopen", side_effect=OSError("offline"))
def test_subscribed_topic_without_machine_id_maps_to_the_live_rig(_mock_urlopen):
    """pdm/integrated_data carries no machineId. Naming it after the topic split the
    live feed away from the compressor_unit_01 history already in Postgres."""
    mqtt_service = MQTTIngestionService(orchestrator=MagicMock())
    mid, _name, _mtype = mqtt_service._identify_asset("pdm/integrated_data", {"parameters": []})
    assert mid == "compressor_unit_01"


@patch("src.services.mqtt_service.persist_mqtt_frame")
def test_mqtt_topic_without_machine_id_uses_topic_slug(mock_persist):
    payload = _sample_payload()
    payload.pop("machineId")
    mqtt_service = MQTTIngestionService(orchestrator=MagicMock())
    mqtt_service._on_message(None, None, _message(payload, topic="pdm/pump/integrated_json"))
    # Unknown component-like topics are retained under their topic slug, but only the
    # configured integrated topics trigger the expensive/alerting DAG.
    assert mqtt_service.orchestrator.run.called is False
    assert "pdm_pump_integrated_json" in mqtt_service.latest_telemetry
    assert mock_persist.called


def test_ntc_second_channel_fills_compressor_temp():
    svc = MQTTIngestionService(orchestrator=MagicMock())
    pair = svc._parse_fragment({"parameters": [
        {"name": "NTC Temperature sensor x 2", "parameters": [41.2, 27.5]},
    ]})
    assert pair["tempMotor"] == 41.2
    assert pair["tempCompressor"] == 27.5
    sequential = svc._parse_fragment({"parameters": [
        {"name": "NTC Temperature sensor x 2", "parameters": [41.2]},
        {"name": "NTC Temperature sensor x 2", "parameters": [27.8]},
    ]})
    assert sequential["tempMotor"] == 41.2
    assert sequential["tempCompressor"] == 27.8
    frame = svc._parse_payload_to_frame(
        {"parameters": [{"name": "NTC Temperature sensor x 2", "parameters": [29.1, 28.4]}]},
        machine_id="compressor_unit_01",
    )
    assert frame.temp_compressor == 28.4
    assert frame.field_was_sent("tempCompressor") is True


def test_derived_runtime_accumulates_while_electrically_running(monkeypatch):
    times = [1000.0]
    monkeypatch.setattr("src.services.mqtt_service.time.monotonic", lambda: times[-1])
    svc = MQTTIngestionService(orchestrator=MagicMock())
    payload = {"parameters": [
        {"name": "NTC Temperature sensor x 2", "parameters": [41.2, 27.5]},
        {"name": "IMU-Acceleration", "parameters": [1.2]},
        {"name": "Energymeter - Ir", "parameters": [12.0]},
        {"name": "Energymeter - Iy", "parameters": [12.0]},
        {"name": "Energymeter - Ib", "parameters": [12.0]},
        {"name": "Energymeter - Power", "parameters": [8.0]},
    ]}
    first = svc._parse_payload_to_frame(payload, machine_id="compressor_unit_01")
    times.append(4600.0)
    second = svc._parse_payload_to_frame(payload, machine_id="compressor_unit_01")
    assert first.field_was_sent("runHours") is True
    assert first.run_hours == 0.0
    assert abs(second.run_hours - 1.0) < 0.01


def test_runtime_accumulates_on_modest_running_current(monkeypatch):
    times = [1000.0]
    monkeypatch.setattr("src.services.mqtt_service.time.monotonic", lambda: times[-1])
    svc = MQTTIngestionService(orchestrator=MagicMock())
    payload = {"parameters": [
        {"name": "NTC Temperature sensor x 2", "parameters": [41.2, 27.5]},
        {"name": "IMU-Acceleration", "parameters": [1.2]},
        {"name": "Energymeter - Ir", "parameters": [1.2]},
        {"name": "Energymeter - Power", "parameters": [0.8]},
        {"name": "Energymeter - Energy", "parameters": [38.0]},
    ]}
    first = svc._parse_payload_to_frame(payload, machine_id="compressor_unit_01")
    times.append(4600.0)
    second = svc._parse_payload_to_frame(payload, machine_id="compressor_unit_01")
    assert first.run_hours == 0.0
    assert second.run_hours > 0.9


def test_percent_fault_confidence_is_normalized_to_fraction():
    svc = MQTTIngestionService(orchestrator=MagicMock())
    fields = svc._parse_fragment({"parameters": [
        {"name": "Motor faults", "parameters": [{"fault_code": "PF001", "description": "Pressure Leakage", "confidence": 90.0}]},
    ]})
    assert fields["motorFaults"][0]["confidence"] == 0.9
    assert fields["motorFaults"][0]["active"] is True


def test_process_running_accumulates_runtime_when_cts_are_zero(monkeypatch):
    times = [1000.0]
    monkeypatch.setattr("src.services.mqtt_service.time.monotonic", lambda: times[-1])
    svc = MQTTIngestionService(orchestrator=MagicMock())
    payload = {"parameters": [
        {"name": "NTC Temperature sensor x 2", "parameters": [49.0, 27.5]},
        {"name": "IMU-Acceleration", "parameters": [0.17]},
        {"name": "Energymeter - Ir", "parameters": [0.0]},
        {"name": "Energymeter - Power", "parameters": [0.0]},
        {"name": "Pressure sensor x 2 [SW]", "parameters": [6.55]},
        {"name": "Microphone - Sound level", "parameters": [4.7]},
    ]}
    first = svc._parse_payload_to_frame(payload, machine_id="compressor_unit_01")
    times.append(4600.0)
    second = svc._parse_payload_to_frame(payload, machine_id="compressor_unit_01")
    assert first.run_hours == 0.0
    assert abs(second.run_hours - 1.0) < 0.01


def test_estimated_kw_from_energy_slope_when_power_register_is_zero(monkeypatch):
    times = [1_700_000_000.0]
    monkeypatch.setattr("src.services.mqtt_service.time.time", lambda: times[-1])
    monkeypatch.setattr("src.services.mqtt_service.time.monotonic", lambda: times[-1])
    monkeypatch.setattr(
        MQTTIngestionService,
        "_seed_energy_history_from_store",
        lambda self, machine_id, now: None,
    )
    svc = MQTTIngestionService(orchestrator=MagicMock())
    payload = {
        "parameters": [
            {"name": "NTC Temperature sensor x 2", "parameters": [49.0, 27.5]},
            {"name": "IMU-Acceleration", "parameters": [0.17]},
            {"name": "Energymeter - Ir", "parameters": [0.0]},
            {"name": "Energymeter - Power", "parameters": [0.0]},
            {"name": "Energymeter - Energy", "parameters": [38.00]},
            {"name": "Pressure sensor x 2 [SW]", "parameters": [6.55]},
            {"name": "Microphone - Sound level", "parameters": [4.7]},
        ]
    }
    first = svc._parse_payload_to_frame(payload, machine_id="compressor_unit_01")
    assert svc._estimate_power_kw(first) == 0.0
    times.append(1_700_000_100.0)
    payload["parameters"][4] = {"name": "Energymeter - Energy", "parameters": [38.25]}
    second = svc._parse_payload_to_frame(payload, machine_id="compressor_unit_01")
    estimated = svc._estimate_power_kw(second)
    assert 8.0 < estimated < 11.0
    svc._publish_latest("compressor_unit_01", second)
    assert svc.latest_telemetry["compressor_unit_01"]["emPower"] == 0.0
    assert svc.latest_telemetry["compressor_unit_01"]["emPowerEstimated"] == estimated


@patch("src.services.mqtt_service.persist_mqtt_frame")
def test_remaining_hours_are_stamped_from_gamma(_mock_persist):
    mock_orch = MagicMock()
    mock_orch.run.return_value = MagicMock(
        cable_check=MagicMock(status="VALID", fault_reason=None),
        defect_localization=MagicMock(defect_code="NORMAL"),
        rul_prediction=MagicMock(rul_operating_hours=432.0),
        model_dump_json=MagicMock(return_value="{}"),
    )
    svc = MQTTIngestionService(orchestrator=mock_orch)
    svc._on_message(None, None, _message(_sample_payload(), topic="pdm/integrated_data"))
    dumped = svc.latest_telemetry["compressor_unit_01"]
    assert dumped["remainingHours"] == 432.0
    assert "remainingHours" in dumped["sourceKeys"]


@patch("src.services.mqtt_service.persist_mqtt_frame")
def test_gamma_defect_is_merged_into_motor_faults(_mock_persist):
    mock_orch = MagicMock()
    mock_orch.run.return_value = MagicMock(
        cable_check=MagicMock(status="VALID", fault_reason=None),
        defect_localization=MagicMock(
            defect_code="PF001",
            defect_name="Compressed-air leakage",
            confidence_percentage=90.0,
        ),
        rul_prediction=MagicMock(rul_operating_hours=48.0),
        model_dump_json=MagicMock(return_value="{}"),
    )
    svc = MQTTIngestionService(orchestrator=mock_orch)
    svc._on_message(None, None, _message(_sample_payload(), topic="pdm/integrated_data"))
    dumped = svc.latest_telemetry["compressor_unit_01"]
    faults = dumped.get("motorFaults") or []
    match = next((item for item in faults if item.get("fault_code") == "PF001"), None)
    assert match is not None
    assert match["active"] is True
    assert abs(float(match["confidence"]) - 0.9) < 1e-9
    assert dumped["remainingHours"] == 48.0
    assert _mock_persist.call_count == 1

    # With the debounce fix: a SINGLE NORMAL result must NOT immediately clear PF001.
    # The fault must persist for at least _FAULT_MIN_HOLD_SECONDS, and then only clear
    # after _NORMAL_CONFIRM_REQUIRED consecutive NORMAL results.
    mock_orch.run.return_value = MagicMock(
        cable_check=MagicMock(status="VALID", fault_reason=None),
        defect_localization=MagicMock(
            defect_code="NORMAL",
            defect_name="Normal",
            confidence_percentage=0.0,
        ),
        rul_prediction=MagicMock(rul_operating_hours=5000.0),
        model_dump_json=MagicMock(return_value="{}"),
    )
    # First NORMAL — should NOT clear PF001 (debounce active, hold not expired)
    svc._on_message(None, None, _message(
        {"parameters": [{"name": "Motor faults", "parameters": [{"fault_code": "MF001", "confidence": 0.0}]}]},
        topic="pdm/integrated_data",
    ))
    after_one_normal = svc.latest_telemetry["compressor_unit_01"]
    still_active = [
        item.get("fault_code") for item in (after_one_normal.get("motorFaults") or [])
        if item.get("active") or float(item.get("confidence") or 0.0) >= 0.5
    ]
    # PF001 must still be held after the very first NORMAL (debounce not yet satisfied)
    assert "PF001" in still_active, (
        "PF001 should still be held after 1 NORMAL result (debounce requires "
        f"{svc._NORMAL_CONFIRM_REQUIRED} consecutive NORMALs and {svc._FAULT_MIN_HOLD_SECONDS}s hold)"
    )

    # Expire the hold window by back-dating _set_at and exhaust the confirm count
    import time as _time_mod
    if svc._last_diagnosis_fault.get("compressor_unit_01"):
        svc._last_diagnosis_fault["compressor_unit_01"]["_set_at"] = (
            _time_mod.monotonic() - svc._FAULT_MIN_HOLD_SECONDS - 1.0
        )
    # Send enough consecutive NORMALs to satisfy _NORMAL_CONFIRM_REQUIRED
    for _ in range(svc._NORMAL_CONFIRM_REQUIRED):
        svc._on_message(None, None, _message(
            {"parameters": [{"name": "Motor faults", "parameters": [{"fault_code": "MF001", "confidence": 0.0}]}]},
            topic="pdm/integrated_data",
        ))
    later = svc.latest_telemetry["compressor_unit_01"]
    active_codes = [
        item.get("fault_code") for item in (later.get("motorFaults") or [])
        if item.get("active") or float(item.get("confidence") or 0.0) >= 0.5
    ]
    assert "PF001" not in active_codes, (
        f"PF001 should be cleared after {svc._NORMAL_CONFIRM_REQUIRED} consecutive NORMALs "
        f"once the {svc._FAULT_MIN_HOLD_SECONDS}s hold window has expired"
    )



def _live_integrated_data_without_imu():
    """Gateway snapshot from pdm/integrated_data: electricity + NTC + mic + pressure, no IMU."""
    return {
        "parameters": [
            {"name": "Energymeter - %THD Vb", "unit": "percentage", "parameters": [2.0]},
            {"name": "Energymeter - %THD Vr", "unit": "percentage", "parameters": [2.0]},
            {"name": "Energymeter - %THD Vy", "unit": "percentage", "parameters": [2.0]},
            {"name": "Energymeter - Average Power Factor", "unit": "pf", "parameters": [1.0]},
            {"name": "Energymeter - Energy", "unit": "Kwh", "parameters": [39.54]},
            {"name": "Energymeter - Frequency", "unit": "Hz", "parameters": [50.097]},
            {"name": "Energymeter - Frequency deviation", "unit": "percentage", "parameters": [0.194]},
            {"name": "Energymeter - Ib", "unit": "Ampere", "parameters": [0.0]},
            {"name": "Energymeter - Ir", "unit": "Ampere", "parameters": [0.0]},
            {"name": "Energymeter - Iy", "unit": "Ampere", "parameters": [0.0]},
            {"name": "Energymeter - Machine load", "unit": "percentage", "parameters": [0.0]},
            {"name": "Energymeter - Power", "unit": "Kw", "parameters": [0.0]},
            {"name": "Energymeter - Vb", "unit": "Volt", "parameters": [396.4]},
            {"name": "Energymeter - Voltage Imbalance", "unit": "percentage", "parameters": [1.059]},
            {"name": "Energymeter - Vr", "unit": "Volt", "parameters": [396.13]},
            {"name": "Energymeter - Vy", "unit": "Volt", "parameters": [402.59]},
            {"name": "Humidity", "unit": "Rh", "parameters": [44.36]},
            {"name": "Microphone - Amplitude 1", "unit": "dB", "parameters": [-3.69]},
            {"name": "Microphone - Amplitude 2", "unit": "dB", "parameters": [-11.04]},
            {"name": "Microphone - Amplitude 3", "unit": "dB", "parameters": [-11.04]},
            {"name": "Microphone - Amplitude 4", "unit": "dB", "parameters": [-11.98]},
            {"name": "Microphone - Amplitude 5", "unit": "dB", "parameters": [-12.19]},
            {"name": "Microphone - Frequency 1", "unit": "Hz", "parameters": [906.2]},
            {"name": "Microphone - Frequency 2", "unit": "Hz", "parameters": [1109.4]},
            {"name": "Microphone - Frequency 3", "unit": "Hz", "parameters": [1000.0]},
            {"name": "Microphone - Frequency 4", "unit": "Hz", "parameters": [2109.4]},
            {"name": "Microphone - Frequency 5", "unit": "Hz", "parameters": [4406.3]},
            {"name": "Microphone - Sound level", "unit": "dB", "parameters": [3.82]},
            {
                "name": "Motor faults",
                "unit": None,
                "data": "integer/string",
                "parameters": [{
                    "fault_code": "PF001",
                    "description": "Pressure Leakage Observed!!",
                    "confidence": 90.0,
                }],
            },
            {"name": "NTC Temperature sensor x 2", "unit": "degree celsius", "parameters": [65.69, 53.13]},
            {"name": "Pressure sensor x 2 [SW]", "unit": None, "parameters": [6.823]},
        ]
    }


def test_live_integrated_data_parses_process_fields_without_inventing_imu():
    from src.models.live_cues import is_operating, is_process_running

    svc = MQTTIngestionService(orchestrator=MagicMock())
    svc._imu_seed_attempted.add("compressor_unit_01")
    frame = svc._parse_payload_to_frame(
        _live_integrated_data_without_imu(),
        machine_id="compressor_unit_01",
    )
    assert frame is not None
    assert frame.field_was_sent("imuAcceleration") is False
    assert frame.temp_motor == 65.69
    assert frame.temp_compressor == 53.13
    assert frame.pressure == 6.823
    assert frame.em_power == 0.0
    assert frame.em_energy == 39.54
    assert frame.sound_level == 3.82
    assert frame.motor_faults[0]["fault_code"] == "PF001"
    assert abs(frame.motor_faults[0]["confidence"] - 0.9) < 1e-9
    assert frame.motor_faults[0]["active"] is True
    assert is_process_running(frame) is True
    assert is_operating(frame) is True


def test_integrated_data_keeps_last_pdm_vibration_rms(monkeypatch):
    times = [1_000.0]
    monkeypatch.setattr("src.services.mqtt_service.time.monotonic", lambda: times[-1])
    svc = MQTTIngestionService(orchestrator=MagicMock())
    svc._parse_payload_to_frame(
        {"parameters": [{"name": "IMU-Acceleration", "unit": "mm/sec", "parameters": [0.1351]}]},
        machine_id="compressor_unit_01",
    )
    times.append(1_040.0)  # past 30s field TTL; IMU is not on integrated_data
    frame = svc._parse_payload_to_frame(
        _live_integrated_data_without_imu(),
        machine_id="compressor_unit_01",
    )
    assert frame.imu_acceleration == 0.1351
    assert frame.field_was_sent("imuAcceleration") is True
    assert frame.vibration_prediction_mode() == "imu_rms"
    times.append(1_000.0 + 301.0)
    expired = svc._parse_payload_to_frame(
        _live_integrated_data_without_imu(),
        machine_id="compressor_unit_01",
    )
    assert expired.field_was_sent("imuAcceleration") is False


def test_pdm_vibration_imu_acceleration_hyphen_axes():
    """Live pdm/vibration names: IMU-Acceleration - X/Y/Z plus overall RMS."""
    svc = MQTTIngestionService(orchestrator=MagicMock())
    frame = svc._parse_payload_to_frame(
        {"parameters": [
            {"name": "IMU-Acceleration", "unit": "mm/sec", "parameters": [0.1371]},
            {"name": "IMU-Acceleration - X", "unit": "mm/sec", "parameters": [0.0745]},
            {"name": "IMU-Acceleration - Y", "unit": "mm/sec", "parameters": [0.0944]},
            {"name": "IMU-Acceleration - Z", "unit": "mm/sec", "parameters": [0.0708]},
        ]},
        machine_id="compressor_unit_01",
    )
    assert frame.imu_acceleration == 0.1371
    assert frame.x_axis_vibration == 0.0745
    assert frame.y_axis_vibration == 0.0944
    assert frame.z_axis_vibration == 0.0708
    assert frame.has_triaxial_axes() is True


def test_integrated_data_keeps_xyz_from_pdm_vibration(monkeypatch):
    """Until integrated_data publishes X/Y/Z, reuse the last pdm/vibration axes."""
    times = [1_000.0]
    monkeypatch.setattr("src.services.mqtt_service.time.monotonic", lambda: times[-1])
    svc = MQTTIngestionService(orchestrator=MagicMock())
    svc._parse_payload_to_frame(
        {"parameters": [
            {"name": "IMU-Acceleration", "unit": "mm/sec", "parameters": [0.1371]},
            {"name": "IMU-Acceleration - X", "unit": "mm/sec", "parameters": [0.0745]},
            {"name": "IMU-Acceleration - Y", "unit": "mm/sec", "parameters": [0.0944]},
            {"name": "IMU-Acceleration - Z", "unit": "mm/sec", "parameters": [0.0708]},
        ]},
        machine_id="compressor_unit_01",
    )
    times.append(1_040.0)
    payload = _live_integrated_data_without_imu()
    payload["parameters"].append(
        {"name": "IMU-Acceleration", "unit": "mm/sec", "parameters": [0.1264]},
    )
    frame = svc._parse_payload_to_frame(payload, machine_id="compressor_unit_01")
    assert frame.imu_acceleration == 0.1264
    assert frame.x_axis_vibration == 0.0745
    assert frame.y_axis_vibration == 0.0944
    assert frame.z_axis_vibration == 0.0708
    assert frame.has_triaxial_axes() is True
    assert frame.vibration_prediction_mode() == "triaxial_rms"
    times.append(1_000.0 + 301.0)
    expired = svc._parse_payload_to_frame(payload, machine_id="compressor_unit_01")
    assert expired.has_triaxial_axes() is False


def test_held_imu_seed_skips_implausible_store_value():
    svc = MQTTIngestionService(orchestrator=MagicMock())
    svc.latest_telemetry["compressor_unit_01"] = {
        "imuAcceleration": 1125.4,
        "sourceKeys": ["imuAcceleration"],
    }
    with patch.object(svc, "_last_plausible_imu_from_store", return_value=None):
        svc._seed_held_imu_once("compressor_unit_01")
    assert "compressor_unit_01" not in svc._last_good_imu
    svc._imu_seed_attempted.clear()
    svc.latest_telemetry["compressor_unit_01"] = {
        "imuAcceleration": 0.21,
        "sourceKeys": ["imuAcceleration"],
    }
    svc._seed_held_imu_once("compressor_unit_01")
    assert svc._last_good_imu["compressor_unit_01"] == 0.21


def test_zone_c_imu_jump_from_healthy_baseline_is_rejected(monkeypatch):
    times = [1_000.0]
    monkeypatch.setattr("src.services.mqtt_service.time.monotonic", lambda: times[-1])
    svc = MQTTIngestionService(orchestrator=MagicMock())
    first = svc._parse_payload_to_frame(
        {"parameters": [{"name": "IMU-Acceleration", "unit": "mm/sec", "parameters": [0.12]}]},
        machine_id="compressor_unit_01",
    )
    assert first.imu_acceleration == 0.12
    times.append(1_002.0)
    spiked = svc._parse_payload_to_frame(
        {"parameters": [
            {"name": "IMU-Acceleration", "unit": "mm/sec", "parameters": [9.59]},
            {"name": "IMU-Acceleration - X", "unit": "mm/sec", "parameters": [19.87]},
            {"name": "IMU-Acceleration - Y", "unit": "mm/sec", "parameters": [4.99]},
            {"name": "IMU-Acceleration - Z", "unit": "mm/sec", "parameters": [9.47]},
        ]},
        machine_id="compressor_unit_01",
    )
    assert spiked.imu_acceleration == 0.12
    assert spiked.has_triaxial_axes() is False


@patch("src.services.mqtt_service.persist_mqtt_frame")
def test_fault_is_held_after_single_normal_packet(_mock_persist):
    """After an error packet, a single NORMAL packet must NOT immediately clear the fault.
    The debounce logic requires N consecutive NORMALs AND the hold window to expire
    before a fault is cleared. This prevents millisecond flickering on the dashboard."""
    mock_orch = MagicMock()
    # 1. First packet produces a fault
    fault_pred = MagicMock(
        cable_check=MagicMock(status="VALID", fault_reason=None),
        defect_localization=MagicMock(
            defect_code="PF001",
            defect_name="Compressed-air leakage",
            confidence_percentage=90.0,
        ),
        rul_prediction=MagicMock(rul_operating_hours=48.0, rul_days=2.0),
        model_dump=MagicMock(return_value={
            "cable_check": {"status": "VALID"},
            "defect_localization": {
                "defect_code": "PF001",
                "defect_name": "Compressed-air leakage",
                "confidence_percentage": 90.0,
            },
            "rul_prediction": {"rul_operating_hours": 48.0, "rul_days": 2.0},
        }),
        model_dump_json=MagicMock(return_value="{}"),
    )
    # 2. Second packet produces normal diagnosis
    normal_pred = MagicMock(
        cable_check=MagicMock(status="VALID", fault_reason=None),
        defect_localization=MagicMock(
            defect_code="NORMAL",
            defect_name="Normal",
            confidence_percentage=0.0,
        ),
        rul_prediction=MagicMock(rul_operating_hours=5000.0, rul_days=208.3),
        model_dump=MagicMock(return_value={
            "cable_check": {"status": "VALID"},
            "defect_localization": {
                "defect_code": "NORMAL",
                "defect_name": "Normal",
                "confidence_percentage": 0.0,
            },
            "rul_prediction": {"rul_operating_hours": 5000.0, "rul_days": 208.3},
        }),
        model_dump_json=MagicMock(return_value="{}"),
    )
    mock_orch.run.side_effect = [fault_pred, normal_pred]

    svc = MQTTIngestionService(orchestrator=mock_orch)

    # Send fault packet
    svc._on_message(None, None, _message(
        {"parameters": [{"name": "Motor faults", "parameters": [{"fault_code": "PF001", "description": "Pressure Leakage", "confidence": 90.0}]}]},
        topic="pdm/motor_fault",
    ))
    assert svc._last_diagnosis["compressor_unit_01"]["severity"] in {"critical", "warning"}
    active = [
        item.get("fault_code") for item in (svc.latest_telemetry["compressor_unit_01"].get("motorFaults") or [])
        if item.get("active") or float(item.get("confidence") or 0.0) >= 0.5
    ]
    assert "PF001" in active

    # Now send normal packet on next refresh
    svc._on_message(None, None, _message(
        {"parameters": [
            {"name": "Motor faults", "parameters": [
                {"fault_code": "MF001", "confidence": 0.0},
                {"fault_code": "MF002", "confidence": 0.0},
                {"fault_code": "MF003", "confidence": 0.0},
            ]},
            {"name": "Pressure sensor x 2 [SW]", "parameters": [7.2]},
            {"name": "IMU-Acceleration", "parameters": [0.15]},
        ]},
        topic="pdm/integrated_data",
    ))

    # Assert that a single NORMAL packet does NOT clear the fault (debounce holds it).
    # The diagnosis must remain in the fault state — no flickering to Normal.
    diag = svc._last_diagnosis["compressor_unit_01"]
    assert diag["severity"] in {"critical", "warning"}, (
        f"Expected fault severity (critical/warning) but got '{diag['severity']}'. "
        "A single NORMAL packet must not clear an active fault (debounce required)."
    )
    assert diag["archetype"] != "NORMAL", (
        "Fault archetype must not flip to NORMAL after a single NORMAL packet."
    )
    active_after = [
        item.get("fault_code") for item in (svc.latest_telemetry["compressor_unit_01"].get("motorFaults") or [])
        if item.get("active") or float(item.get("confidence") or 0.0) >= 0.5
    ]
    # PF001 must still be in active faults because the debounce hold is still active
    assert "PF001" in active_after, (
        "PF001 must remain in active faults after a single NORMAL packet. "
        f"Found active faults: {active_after}"
    )

