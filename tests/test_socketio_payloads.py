"""Socket.IO payload shape expected by the Next.js RealtimeProvider."""

from src.api.socketio_server import (
    _machine_meta,
    _upstream_payload,
    set_mqtt_service_for_sio,
    to_frontend_telemetry,
)


def test_to_frontend_telemetry_nests_flat_python_aliases():
    mapped = to_frontend_telemetry(
        {
            "machineId": "compressor_unit_01",
            "timestamp": 1_700_000_000_000,
            "imuAcceleration": 2.4,
            "tempMotor": 41.2,
            "tempCompressor": 36.1,
            "emIr": 12.4,
            "emPower": 8.1,
            "soundLevel": 64.0,
            "rpm": 1480,
        },
        "compressor_unit_01",
    )
    assert mapped is not None
    assert mapped["machineId"] == "compressor_unit_01"
    assert mapped["temperature"]["motor"] == 41.2
    assert mapped["energyMeter"]["Ir"] == 12.4
    assert mapped["microphone"]["soundLevel"] == 64.0
    assert mapped["energyMeter"]["power"] == 8.1
    assert mapped["energyMeter"]["powerEstimated"] is False


def test_to_frontend_telemetry_keeps_meter_kw_when_register_is_zero():
    mapped = to_frontend_telemetry(
        {
            "machineId": "compressor_unit_01",
            "timestamp": 1_700_000_000_000,
            "emPower": 0.0,
            "emPowerEstimated": 7.4,
            "emMachineLoad": 0.0,
            "emMachineLoadEstimated": 20.0,
            "emIr": 0.0,
            "emIy": 0.0,
            "emIb": 0.0,
        },
        "compressor_unit_01",
    )
    assert mapped is not None
    assert mapped["energyMeter"]["power"] == 0.0
    assert mapped["energyMeter"]["powerEstimated"] is True
    assert mapped["energyMeter"]["estimatedPower"] == 7.4
    assert mapped["energyMeter"]["machineLoad"] == 0.0


def test_to_frontend_telemetry_uses_nameplate_load_not_ct_percent():
    mapped = to_frontend_telemetry(
        {
            "machineId": "compressor_unit_01",
            "timestamp": 1_700_000_000_000,
            "emPower": 2.42,
            "emMachineLoad": 88.0,
            "emIr": 4.4,
            "emIy": 4.2,
            "emIb": 4.1,
        },
        "compressor_unit_01",
    )
    assert mapped is not None
    assert mapped["energyMeter"]["power"] == 2.42
    assert mapped["energyMeter"]["machineLoad"] == 6.5



def test_to_frontend_telemetry_uses_ambient_ntc_as_compressor():
    mapped = to_frontend_telemetry(
        {
            "machineId": "compressor_unit_01",
            "timestamp": 1_700_000_000_000,
            "tempMotor": 41.2,
            "tempAmbient": 27.5,
            "sourceKeys": ["tempMotor", "tempAmbient"],
        },
        "compressor_unit_01",
    )
    assert mapped is not None
    assert mapped["temperature"]["compressor"] == 27.5
    assert "tempAmbient" in mapped["availableFields"]


def test_upstream_payload_uses_frontend_state_field():
    class _Mqtt:
        is_connected = True
        last_message_at_ms = 123

    set_mqtt_service_for_sio(_Mqtt())
    payload = _upstream_payload()
    assert payload["state"] == "connected"
    assert payload["lastMessageAt"] == 123
    assert "since" in payload

    _Mqtt.is_connected = False
    assert _upstream_payload()["state"] == "offline"
    set_mqtt_service_for_sio(None)


def test_machine_meta_places_assets_on_the_floor():
    meta = _machine_meta({"id": "compressor_unit_01", "name": "Compressor"}, index=1)
    assert meta["mapX"] == 55.0
    assert meta["mapY"] == 45.0
    assert meta["ratedRpm"] == 1480
    assert meta["location"] == "Factory Floor 1"


def test_to_frontend_diagnosis_maps_live_pipeline():
    from src.api.socketio_server import to_frontend_diagnosis

    card = to_frontend_diagnosis("compressor_unit_01", {
        "cable_check": {"status": "VALID", "pattern_recognition_status": "STABLE"},
        "defect_localization": {
            "defect_code": "PF001",
            "defect_name": "Pressure Leakage Observed",
            "diagnosis_method": "iso11011_gateway_pf",
            "expert_repair_guidance": {
                "immediate_field_triage": "Walk the compressed-air line.",
                "root_cause_mechanism": "Leak at fitting",
            },
        },
        "rul_prediction": {"rul_days": 2.0, "rul_operating_hours": 48},
        "electrical_health": {"isolated_failure_domain": "MECHANICAL", "voltage_unbalance_pct": 1.09},
        "thermal_de_weathering": {"delta_temperature_c": 11.0},
        "cmms_work_order": {"work_order_id": "WO-1"},
    })
    assert card["machineId"] == "compressor_unit_01"
    assert card["archetype"] == "PF001"
    assert card["headline"] == "Compressed-air leak (PF001) Identified"
    assert card["severity"] == "critical"
    assert "Agent Gamma" in card["summary"]
    assert card["faultExplanation"]["rootCause"] == "Leak at fitting"
    assert "piping" in card["faultExplanation"]["whatIsIt"].lower()
    assert "bearing" in card["faultExplanation"]["whatIsIt"].lower()
    assert "gateway" in card["faultExplanation"]["whyShowing"].lower()
    assert "vibration" in card["faultExplanation"]["notThis"].lower()
    assert "AIR-LEAK-KIT" in card["faultExplanation"]["sparePart"]
    opts = card["repairOptions"]
    assert opts[0]["title"] == "Find and seal the air leak"
    assert "ultrasonic" in opts[0]["steps"][0].lower() or "Walk the compressed-air line" in opts[0]["steps"][0]
    assert "vibration pen" not in (opts[0].get("partsOrTools") or "").lower()
    assert "leak detector" in (opts[0].get("partsOrTools") or "").lower()
