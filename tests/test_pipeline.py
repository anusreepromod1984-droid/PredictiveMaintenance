"""
End-to-End Integration Tests for APMS 4-Agent LangGraph Pipeline
"""

import pytest
from src.schemas.telemetry import TelemetryFrame, HarmonicPeak
from src.agents.orchestrator import APMSOrchestrator


def _frame(**overrides):
    payload = dict(
        machineId="compressor_unit_01",
        imuAcceleration=7.9051,
        rpm=1480,
        tempMotor=28.94,
        tempCompressor=29.06,
        tempAmbient=25.0,
        humidity=45.2,
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
        ],
    )
    payload.update(overrides)
    return TelemetryFrame(**payload)


def test_full_langgraph_pipeline():
    orchestrator = APMSOrchestrator()
    response = orchestrator.run(_frame())

    assert response.machine_id == "compressor_unit_01"
    assert response.cable_check.status == "VALID"
    assert response.defect_localization is not None
    assert response.rul_prediction is not None
    assert response.rul_prediction.rul_days > 0.0
    assert response.cmms_work_order is not None
    assert response.cmms_work_order.work_order_id.startswith("4")
    assert response.cmms_work_order.is_advisory_draft is False
    assert response.cmms_work_order.reserved_warehouse_bin == "BIN-SHIM-04"
    assert response.cmms_work_order.sourcing_intelligence is not None
    assert response.cmms_work_order.sourcing_intelligence.sourcing_recommendation == "RESERVE_FROM_STORES"
    assert response.electrical_health is not None
    assert response.electrical_health.isolated_failure_domain in {"MECHANICAL", "ELECTRICAL", "MIXED", "ENVIRONMENTAL"}
    assert response.rul_prediction.method.startswith("physics_wear_law_plus_weibull")
    assert response.rul_prediction.bearing_rul_days is not None
    assert response.iso_vibration_zone == "D"
    assert response.iso_machine_group == "2"


def test_alpha_halt_returns_sensor_card_without_pdm_wo():
    orchestrator = APMSOrchestrator()
    response = orchestrator.run(_frame(plcParam12Status=1, imuAcceleration=0.0, emIr=20.0, emPower=18.0))
    assert response.cable_check.status == "HARDWARE_CABLE_FAULT"
    assert response.cable_check.alert_suppressed is True
    assert response.cable_check.buffer_write == "rejected"
    assert response.overall_health_status == "SENSOR_FAULT"
    assert response.card_type == "SENSOR_FAULT"
    assert response.cmms_work_order is None
    assert response.defect_localization is None


def test_healthy_machine_skips_delta():
    orchestrator = APMSOrchestrator()
    orchestrator.reset_machine("compressor_unit_01")
    response = orchestrator.run(_frame(
        imuAcceleration=1.2,
        emVoltageImbalance=0.8,
        tempMotor=40.0,
        tempAmbient=25.0,
        emMachineLoad=60.0,
        vibrationHarmonics=[],
    ))
    assert response.cable_check.status == "VALID"
    assert response.defect_localization is None or response.defect_localization.defect_code == "NORMAL"
    assert response.cmms_work_order is None
    assert response.skip_reason == "HEALTHY"
    assert response.card_type == "MONITOR"
    assert response.iso_vibration_zone == "A"


def test_beta_domain_electrical_when_vuf_high():
    from src.agents.agent_beta import AgentBeta
    from src.schemas.predictions import CableCheckStatus
    beta = AgentBeta()
    frame = _frame(emVoltageImbalance=4.2, imuAcceleration=1.8, tempMotor=40.0)
    cable = CableCheckStatus(status="VALID", alert_suppressed=False, pattern_recognition_status="STABLE")
    _, electrical = beta.process(frame, cable)
    assert electrical.isolated_failure_domain == "ELECTRICAL"


def test_buffer_reset_clears_alpha_history():
    orchestrator = APMSOrchestrator()
    orchestrator.run(_frame())
    baseline = orchestrator.reset_machine("compressor_unit_01")
    assert "compressor_unit_01" in baseline
    assert orchestrator.alpha.store.length("compressor_unit_01") == 0
