"""
Unit Tests for Agent Alpha (NAMUR NE43 Cable Fault Gatekeeper)
"""

import pytest
from src.schemas.telemetry import TelemetryFrame, HarmonicPeak
from src.agents.agent_alpha import AgentAlpha


@pytest.fixture
def valid_telemetry_frame():
    return TelemetryFrame(
        machineId="compressor_unit_01",
        imuAcceleration=7.9051,
        rpm=1480,
        tempMotor=28.94,
        tempCompressor=29.06,
        emIr=28.8,
        emIy=29.1,
        emIb=28.9,
        emVr=395.62,
        emVy=404.55,
        emVb=397.07,
        emMachineLoad=82.5,
        emVoltageImbalance=1.371,
        emPower=18.4,
        soundLevel=-7.1,
        magRoll=0.12,
        magPitch=0.05,
        magYaw=0.01,
        runHours=1420.5,
        vibrationHarmonics=[
            HarmonicPeak(frequency=109.4, amplitude=-15.26),
            HarmonicPeak(frequency=218.8, amplitude=-19.05)
        ]
    )


def test_agent_alpha_valid_cable(valid_telemetry_frame):
    agent = AgentAlpha()
    status = agent.process(valid_telemetry_frame)
    
    assert status.status == "VALID"
    assert status.namur_ne43_signal_valid is True
    assert status.alert_suppressed is False


def test_agent_alpha_plc_register_fault(valid_telemetry_frame):
    valid_telemetry_frame.plc_param_12_status = 1  # Simulated wire cut register
    agent = AgentAlpha()
    status = agent.process(valid_telemetry_frame)
    
    assert status.status == "HARDWARE_CABLE_FAULT"
    assert status.namur_ne43_signal_valid is False
    assert status.alert_suppressed is True


def test_alpha_pattern_3_ignores_leftover_rms_spikes():
    from src.services.ring_buffer import MemoryRingBuffer

    store = MemoryRingBuffer(window_size=24)
    agent = AgentAlpha(window_size=24, store=store)
    mid = "alpha_spike_filter"
    for value in [0.12] * 10 + [19.8, 9.6] + [0.13] * 6:
        store.append(mid, {
            "vibration": value,
            "temp_motor": 40.0,
            "temp_compressor": 36.0,
            "current_ir": 0.0,
            "current_iy": 0.0,
            "current_ib": 0.0,
            "power": 0.0,
            "rpm": 0.0,
        })
    frame = TelemetryFrame(
        machineId=mid,
        imuAcceleration=0.12,
        tempMotor=40.0,
        tempCompressor=36.0,
        emIr=0.0,
        emPower=0.0,
        pressure=6.5,
        soundLevel=3.0,
        sourceKeys=["imuAcceleration", "tempMotor", "tempCompressor", "emIr", "emPower", "pressure", "soundLevel"],
    )
    status = agent.process(frame)
    assert status.status == "VALID"
    assert status.pattern_recognition_status != "SUDDEN_JUMP_FLAT"


def test_alpha_pattern_3_ignores_leftover_rms_spikes():
    from src.services.ring_buffer import MemoryRingBuffer

    store = MemoryRingBuffer(window_size=24)
    agent = AgentAlpha(window_size=24, store=store)
    mid = "alpha_spike_filter"
    for value in [0.12] * 10 + [19.8, 9.6] + [0.13] * 6:
        store.append(mid, {
            "vibration": value,
            "temp_motor": 40.0,
            "temp_compressor": 36.0,
            "current_ir": 0.0,
            "current_iy": 0.0,
            "current_ib": 0.0,
            "power": 0.0,
            "rpm": 0.0,
        })
    frame = TelemetryFrame(
        machineId=mid,
        imuAcceleration=0.12,
        tempMotor=40.0,
        tempCompressor=36.0,
        emIr=0.0,
        emPower=0.0,
        pressure=6.5,
        soundLevel=3.0,
        sourceKeys=["imuAcceleration", "tempMotor", "tempCompressor", "emIr", "emPower", "pressure", "soundLevel"],
    )
    status = agent.process(frame)
    assert status.status == "VALID"
    assert status.pattern_recognition_status != "SUDDEN_JUMP_FLAT"
