"""Agent Alpha 11-point hardware fault suite (live LangGraph gatekeeper)."""

from src.agents.agent_alpha import AgentAlpha
from src.schemas.telemetry import TelemetryFrame
from src.services.ring_buffer import MemoryRingBuffer


def _frame(**overrides) -> TelemetryFrame:
    payload = dict(
        machineId="compressor_unit_01",
        imuAcceleration=1.25,
        rpm=1480,
        tempMotor=52.0,
        tempCompressor=50.0,
        emIr=18.0,
        emIy=18.0,
        emIb=18.0,
        emVr=415.0,
        emVy=415.0,
        emVb=415.0,
        emMachineLoad=70.0,
        emVoltageImbalance=0.0,
        emPower=11.92,
        emAveragePowerFactor=0.92,
        runHours=150.0,
        emEnergy=1200.0,
        bearingModel="SKF-6208-2Z",
        plcParam12Status=0,
    )
    payload.update(overrides)
    return TelemetryFrame(**payload)


def _alpha() -> AgentAlpha:
    return AgentAlpha(store=MemoryRingBuffer(30))


def test_sensor_out_of_range_negative_vibration():
    status = _alpha().process(_frame(imuAcceleration=-0.8))
    assert status.status == "SENSOR_OUT_OF_RANGE"
    assert status.alert_suppressed is True
    assert status.buffer_write == "rejected"


def test_sensor_out_of_range_negative_current_and_nan():
    status = _alpha().process(_frame(emIr=-2.0))
    assert status.status == "SENSOR_OUT_OF_RANGE"
    nan_status = _alpha().process(_frame(tempMotor=float("nan")))
    assert nan_status.status == "SENSOR_OUT_OF_RANGE"


def test_sensor_frozen_flatline_any_field():
    agent = _alpha()
    last = None
    for _ in range(15):
        last = agent.process(_frame(imuAcceleration=1.25))
    assert last.status == "SENSOR_FROZEN_FLATLINE"
    assert last.alert_suppressed is True
    assert last.buffer_write == "rejected"
    assert agent.store.length("compressor_unit_01") == 14


def test_hardware_cable_cross_domain():
    status = _alpha().process(_frame(imuAcceleration=0.0, tempMotor=45.0))
    assert status.status == "HARDWARE_CABLE_FAULT"
    assert "Cross-Domain" in (status.fault_reason or "")


def test_hardware_cable_step_drop():
    agent = _alpha()
    first = agent.process(_frame(imuAcceleration=2.4))
    assert first.status == "VALID"
    second = agent.process(_frame(imuAcceleration=0.0, tempMotor=52.0))
    assert second.status == "HARDWARE_CABLE_FAULT"
    assert "Step-Drop" in (second.fault_reason or "")


def test_field_not_present_product_a_block():
    frame = TelemetryFrame(
        machineId="compressor_unit_01",
        emIr=18.0, emIy=18.0, emIb=18.0,
        emVr=415.0, emVy=415.0, emVb=415.0,
        emMachineLoad=70.0,
        emVoltageImbalance=0.0,
        emPower=11.92,
        rpm=1480,
    )
    assert "product_a" in frame.missing_blocks or set(frame.missing_blocks) >= {"imuAcceleration", "tempMotor"}
    status = _alpha().process(frame)
    assert status.status == "FIELD_NOT_PRESENT"
    assert status.alert_suppressed is True


def test_field_not_present_partial_product_a_block():
    """IMU-Acceleration is intermittent. Temp + electricity must still pass Alpha."""
    frame = TelemetryFrame(
        machineId="compressor_unit_01",
        tempMotor=31.19,
        rpm=1480,
        emIr=0.0, emIy=0.0, emIb=0.0,
        emVr=394.53, emVy=400.16, emVb=393.41,
        emMachineLoad=0.0,
        emVoltageImbalance=1.043,
        emPower=0.0,
    )
    assert frame.missing_blocks == ["imuAcceleration"]
    assert frame.field_was_sent("tempMotor") is True
    assert frame.field_was_sent("imuAcceleration") is False
    status = _alpha().process(frame)
    assert status.status == "VALID"
    assert status.alert_suppressed is False


def test_field_not_present_product_b_block():
    frame = TelemetryFrame(
        machineId="compressor_unit_01",
        imuAcceleration=1.25,
        tempMotor=52.0,
        tempCompressor=50.0,
        rpm=1480,
    )
    assert "product_b" in frame.missing_blocks
    status = _alpha().process(frame)
    assert status.status == "FIELD_NOT_PRESENT"


def test_counter_non_monotonic_run_hours_and_energy():
    agent = _alpha()
    assert agent.process(_frame(runHours=150.0, emEnergy=1200.0)).status == "VALID"
    hours = agent.process(_frame(runHours=100.0, emEnergy=1250.0))
    assert hours.status == "COUNTER_NON_MONOTONIC"
    assert "runHours" in (hours.fault_reason or "")

    agent2 = _alpha()
    assert agent2.process(_frame(runHours=150.0, emEnergy=1200.0)).status == "VALID"
    energy = agent2.process(_frame(runHours=151.0, emEnergy=1100.0))
    assert energy.status == "COUNTER_NON_MONOTONIC"
    assert "emEnergy" in (energy.fault_reason or "")


def test_power_calculation_mismatch():
    status = _alpha().process(_frame(
        emIr=20.0, emIy=20.0, emIb=20.0,
        emVr=400.0, emVy=400.0, emVb=400.0,
        emAveragePowerFactor=0.9,
        emPower=2.0,
        emVoltageImbalance=0.0,
    ))
    assert status.status == "POWER_CALCULATION_MISMATCH"
    assert status.alert_suppressed is True


def test_derived_value_mismatch():
    status = _alpha().process(_frame(
        emVr=400.0, emVy=400.0, emVb=400.0,
        emVoltageImbalance=5.5,
    ))
    assert status.status == "DERIVED_VALUE_MISMATCH"


def test_rpm_sensor_mismatch():
    status = _alpha().process(_frame(rpm=0.0, imuAcceleration=1.25))
    assert status.status == "RPM_SENSOR_MISMATCH"
    assert status.alert_suppressed is True


def test_static_metadata_changed():
    agent = _alpha()
    assert agent.process(_frame(bearingModel="SKF-6208-2Z")).status == "VALID"
    status = agent.process(_frame(bearingModel="WRONG-BEARING-MODEL"))
    assert status.status == "STATIC_METADATA_CHANGED"


def test_temp_correlation_mismatch():
    status = _alpha().process(_frame(tempMotor=85.0, tempCompressor=25.0))
    assert status.status == "TEMP_CORRELATION_MISMATCH"


def test_temp_correlation_skipped_when_compressor_not_sent():
    payload = dict(
        machineId="compressor_unit_01",
        imuAcceleration=1.25,
        rpm=1480,
        tempMotor=85.0,
        emIr=18.0,
        emIy=18.0,
        emIb=18.0,
        emVr=415.0,
        emVy=415.0,
        emVb=415.0,
        emMachineLoad=70.0,
        emVoltageImbalance=0.0,
        emPower=11.92,
        emAveragePowerFactor=0.92,
        runHours=150.0,
        emEnergy=1200.0,
        bearingModel="SKF-6208-2Z",
        plcParam12Status=0,
    )
    payload["sourceKeys"] = list(payload.keys())
    frame = TelemetryFrame(**payload)
    assert frame.field_was_sent("tempCompressor") is False
    status = _alpha().process(frame)
    assert status.status == "VALID"


def test_healthy_frame_is_valid_and_accepted():
    status = _alpha().process(_frame())
    assert status.status == "VALID"
    assert status.alert_suppressed is False
    assert status.buffer_write == "accepted"


def test_idle_machine_rpm_zero_is_not_tachometer_fault():
    status = _alpha().process(_frame(
        rpm=0.0,
        emIr=0.4, emIy=0.4, emIb=0.4,
        emPower=0.2,
        imuAcceleration=0.05,
        tempMotor=28.0,
        tempCompressor=27.0,
    ))
    assert status.status == "VALID"
