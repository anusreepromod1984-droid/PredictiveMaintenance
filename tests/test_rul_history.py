from src.agents.agent_gamma import AgentGamma
from src.models.rul_history import blend_rul_hours, rms_for_rul
from src.schemas.telemetry import TelemetryFrame


def test_rms_for_rul_uses_history_when_imu_absent():
    rms, source = rms_for_rul(0.0, False, [{"imuAcceleration": 0.1914, "rulOperatingHours": 400.0}])
    assert rms == 0.1914
    assert source == "held_history"


def test_rms_for_rul_does_not_mix_incompatible_spikes():
    rms, source = rms_for_rul(7.9, True, [{"imuAcceleration": 0.19}])
    assert rms == 7.9
    assert source == "current"


def test_blend_rul_decays_previous_clock():
    hours, used = blend_rul_hours(
        400.0,
        [{"rulOperatingHours": 500.0, "runHours": 100.0}],
        run_hours=110.0,
    )
    assert used is True
    # 0.65*400 + 0.35*(500-10) = 260 + 171.5 = 431.5
    assert abs(hours - 431.5) < 0.2


def test_gamma_rul_without_imu_uses_stored_rms():
    frame = TelemetryFrame(
        machineId="compressor_unit_01",
        imuAcceleration=0.0,
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
        runHours=120.0,
        sourceKeys=["tempMotor", "emPower", "pressure"],
    )
    assert frame.field_was_sent("imuAcceleration") is False
    _defect, without = AgentGamma().process(frame, history=[])
    _defect, with_hist = AgentGamma().process(
        frame,
        history=[{"imuAcceleration": 0.1914, "rulOperatingHours": 480.0, "runHours": 119.0}],
    )
    assert with_hist.method.endswith("_with_history")
    # 0 mm/s physics RUL is far longer than 0.19 mm/s with a prior clock.
    assert with_hist.rul_operating_hours < without.rul_operating_hours
