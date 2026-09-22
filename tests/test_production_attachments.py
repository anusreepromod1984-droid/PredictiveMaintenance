"""
Unit Tests for APMS Production Attachments
1. Triggered Waveform Execution in Agent Gamma
2. Elimination of MF001 Catch-All
3. Open Work Order De-Duplication in Agent Delta (skip_reason='OPEN_WO')
4. Persistent Event & Diagnosis Historian with trace_id
5. Alpha NAMUR Loop Current & Beta PQ Attachments
"""

import pytest
from src.schemas.telemetry import TelemetryFrame, HarmonicPeak
from src.agents.orchestrator import APMSOrchestrator
from src.models.fault_classifier import FaultClassifierEngine
from src.services.event_historian import event_historian
from src.agents.open_wo_tracker import wo_tracker


def _base_frame(**overrides) -> TelemetryFrame:
    payload = {
        "machineId": "test_machine_01",
        "imuAcceleration": 1.2,
        "rpm": 1480,
        "tempMotor": 45.0,
        "tempCompressor": 48.0,
        "tempAmbient": 25.0,
        "humidity": 45.0,
        "emIr": 12.0,
        "emIy": 12.0,
        "emIb": 12.0,
        "emVr": 400.0,
        "emVy": 400.0,
        "emVb": 400.0,
        "emMachineLoad": 75.0,
        "emVoltageImbalance": 0.8,
        "emPower": 7.65,
        "soundLevel": 72.0,
        "magRoll": 0.1,
        "magPitch": 0.1,
        "magYaw": 0.0,
        "runHours": 500.0,
        "bearingModel": "SKF-6208",
        "plcParam12Status": 0,
        "vibrationHarmonics": [],
    }
    payload.update(overrides)
    return TelemetryFrame(**payload)


def test_alpha_namur_loop_current_checks():
    """Verify NAMUR NE43 analog 4-20mA loop current checks (<3.6 mA break, >21.0 mA short)."""
    orchestrator = APMSOrchestrator()
    
    # 1. Broken wire (< 3.6 mA)
    frame_cut = _base_frame(loopCurrentMa=2.4)
    res_cut = orchestrator.run(frame_cut)
    assert res_cut.cable_check.status == "HARDWARE_CABLE_FAULT"
    assert res_cut.cable_check.alert_suppressed is True
    assert "below 3.6 mA" in res_cut.cable_check.fault_reason or "below" in res_cut.cable_check.fault_reason

    # 2. Short circuit (> 21.0 mA)
    frame_short = _base_frame(loopCurrentMa=22.8)
    res_short = orchestrator.run(frame_short)
    assert res_short.cable_check.status == "HARDWARE_CABLE_FAULT"
    assert res_short.cable_check.alert_suppressed is True

    # 3. Valid loop current (4-20 mA)
    frame_valid = _base_frame(loopCurrentMa=12.5)
    res_valid = orchestrator.run(frame_valid)
    assert res_valid.cable_check.status == "VALID"


def test_gamma_triggered_waveform_always_runs():
    """Verify that light MQTT packets without raw waveforms trigger physical waveform synthesis."""
    frame = _base_frame(
        imuAcceleration=5.2,
        vibrationHarmonics=[HarmonicPeak(frequency=162.4, amplitude=-12.0)]
    )
    assert frame.waveform_z is None
    
    waveform = frame.ensure_triggered_waveform()
    assert waveform is not None
    assert len(waveform) >= 128
    assert frame.waveform_z is not None


def test_fault_classifier_does_not_overcall_mf001():
    """Verify that unclassified mechanical vibration is NOT labeled as Mechanical Looseness (MF001)."""
    engine = FaultClassifierEngine()

    # Case 1: Healthy baseline -> NORMAL
    normal = engine.classify_defect(
        vibration_rms=1.2,
        matched_signal_fault="NONE",
        voltage_unbalance=0.8,
        load_pct=70.0,
        temp_motor=45.0,
        temp_ambient=25.0,
        pattern="STABLE",
        isolated_domain="MECHANICAL"
    )
    assert normal["defect_code"] == "NORMAL"

    # Case 2: Elevated vibration without looseness signatures -> ANOMALY_UNCLASSIFIED
    elevated = engine.classify_defect(
        vibration_rms=5.5,
        matched_signal_fault="NONE",
        voltage_unbalance=0.8,
        load_pct=75.0,
        temp_motor=50.0,
        temp_ambient=25.0,
        pattern="STABLE",
        isolated_domain="MECHANICAL"
    )
    assert elevated["defect_code"] != "MF001"
    assert elevated["defect_code"] == "ANOMALY_UNCLASSIFIED"

    # Case 3: Genuine looseness signature -> MF001
    looseness = engine.classify_defect(
        vibration_rms=5.0,
        matched_signal_fault="MF001_MECHANICAL_LOOSENESS",
        voltage_unbalance=0.8,
        load_pct=75.0,
        temp_motor=50.0,
        temp_ambient=25.0,
        pattern="NOISE_EXPANSION",
        isolated_domain="MECHANICAL"
    )
    assert looseness["defect_code"] == "MF001"


def test_open_wo_tracker_suppresses_duplicates():
    """Verify Agent Delta de-duplication: first run creates draft, subsequent runs set skip_reason=OPEN_WO."""
    orchestrator = APMSOrchestrator()
    machine_id = "test_wo_dedup_01"
    orchestrator.reset_machine(machine_id)

    frame = _base_frame(
        machineId=machine_id,
        imuAcceleration=6.5,
        vibrationHarmonics=[HarmonicPeak(frequency=162.4, amplitude=-8.0)]
    )

    # 1. First run creates the initial work order draft
    res1 = orchestrator.run(frame)
    assert res1.defect_localization is not None
    assert res1.cmms_work_order is not None
    assert res1.cmms_work_order.is_duplicate is False
    assert res1.skip_reason is None

    # 2. Second run for the same ongoing defect suppresses duplicate ticket
    res2 = orchestrator.run(frame)
    assert res2.cmms_work_order is not None
    assert res2.cmms_work_order.is_duplicate is True
    assert res2.skip_reason == "OPEN_WO"

    # 3. Post-repair reset clears open ticket
    orchestrator.reset_machine(machine_id)
    assert wo_tracker.has_open_work_order(machine_id, res1.defect_localization.defect_code) is None

    # 4. Third run can generate work order draft again
    res3 = orchestrator.run(frame)
    assert res3.cmms_work_order is not None
    assert res3.cmms_work_order.is_duplicate is False


def test_event_historian_persists_trace_id_and_events():
    """Verify that execution receives a trace_id and fault events are persisted."""
    orchestrator = APMSOrchestrator()
    machine_id = "test_historian_01"

    # 1. Fault diagnosis run
    frame_fault = _base_frame(machineId=machine_id, imuAcceleration=7.8)
    res_diag = orchestrator.run(frame_fault)
    assert res_diag.trace_id.startswith("trc_apms_")
    
    recent_diags = event_historian.get_recent_diagnoses(machine_id)
    assert len(recent_diags) >= 1
    assert recent_diags[0]["trace_id"] == res_diag.trace_id

    # 2. Sensor Cable Break Halt
    frame_halt = _base_frame(machineId=machine_id, plcParam12Status=99)
    res_halt = orchestrator.run(frame_halt)
    assert res_halt.overall_health_status == "SENSOR_FAULT"
    
    recent_events = event_historian.get_recent_events(machine_id)
    assert len(recent_events) >= 1
    assert recent_events[0]["trace_id"] == res_halt.trace_id
    assert recent_events[0]["event_type"] == "SENSOR_HALT"


def test_beta_electrical_domain_incorporates_thd_and_iuf():
    """Verify that high THD or current unbalance flips isolated domain to ELECTRICAL."""
    from src.agents.agent_beta import AgentBeta
    from src.schemas.predictions import CableCheckStatus

    beta = AgentBeta()
    cable = CableCheckStatus(status="VALID", alert_suppressed=False, pattern_recognition_status="STABLE")

    # High THD (12.5% > 8.0%)
    frame_thd = _base_frame(emThd=12.5, imuAcceleration=1.5)
    _, elec_thd = beta.process(frame_thd, cable)
    assert elec_thd.isolated_failure_domain == "ELECTRICAL"

    # High current imbalance (18.0% > 10.0%)
    frame_iuf = _base_frame(emCurrentImbalance=18.0, imuAcceleration=1.5)
    _, elec_iuf = beta.process(frame_iuf, cable)
    assert elec_iuf.isolated_failure_domain == "ELECTRICAL"


def test_event_historian_filters_redundant_normal_and_keeps_faults_resolutions():
    """Verify that ONLY active defects and alerts are recorded in diagnosis history."""
    import uuid
    orchestrator = APMSOrchestrator()
    machine_id = f"test_filter_hist_{uuid.uuid4().hex[:8]}"

    # Step 1: Healthy runs -> not persisted in diagnosis history
    frame_healthy = _base_frame(machineId=machine_id)
    orchestrator.run(frame_healthy)
    orchestrator.run(frame_healthy)
    diags_1 = event_historian.get_recent_diagnoses(machine_id)
    assert len(diags_1) == 0, "Healthy runs must not be in diagnosis history"

    # Step 2: Fault injected (severe vibration Zone D) -> must be persisted
    frame_fault = _base_frame(
        machineId=machine_id,
        imuAcceleration=7.8,
    )
    res_fault = orchestrator.run(frame_fault)
    assert res_fault.overall_health_status in ("CRITICAL", "WARNING", "FAULT", "ALERT", "DEGRADED")
    diags_2 = event_historian.get_recent_diagnoses(machine_id)
    assert len(diags_2) == 1
    assert diags_2[0]["trace_id"] == res_fault.trace_id
    assert diags_2[0]["health_status"] == res_fault.overall_health_status

    # Step 3: Restored to healthy -> subsequent runs are not persisted
    for _ in range(5):
        orchestrator.run(frame_healthy)
    diags_3 = event_historian.get_recent_diagnoses(machine_id)
    assert len(diags_3) == 1, "Only the fault record remains in diagnosis history"

