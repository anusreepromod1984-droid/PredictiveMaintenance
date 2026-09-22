from src.models.live_cues import acoustic_two_x, is_operating, is_process_running, max_thd_pct, three_phase_kw
from src.schemas.telemetry import TelemetryFrame


def _idle(**overrides) -> TelemetryFrame:
    payload = dict(
        machineId="compressor_unit_01",
        imuAcceleration=0.1251,
        tempMotor=29.56,
        tempCompressor=29.69,
        tempAmbient=29.69,
        humidity=48.09,
        emIr=0.0,
        emIy=0.0,
        emIb=0.0,
        emVr=399.23,
        emVy=404.91,
        emVb=399.99,
        emMachineLoad=0.0,
        emVoltageImbalance=0.882,
        emPower=0.0,
        emPowerFactor=1.0,
        emEnergy=37.95,
        emThdVr=2.1,
        emThdVy=2.1,
        emThdVb=2.1,
        emFrequency=49.971,
        emFrequencyDeviation=-0.058,
        pressure=3.781,
        soundLevel=-4.33,
        micHarmonics=[
            {"frequency": 93.7, "amplitude": -11.54},
            {"frequency": 218.8, "amplitude": -21.77},
            {"frequency": 312.5, "amplitude": -27.74},
            {"frequency": 437.5, "amplitude": -27.93},
            {"frequency": 781.2, "amplitude": -28.7},
        ],
        sourceKeys=[
            "imuAcceleration", "tempMotor", "tempCompressor", "tempAmbient", "humidity",
            "emIr", "emIy", "emIb", "emVr", "emVy", "emVb", "emMachineLoad",
            "emVoltageImbalance", "emPower", "emPowerFactor", "emEnergy",
            "emThdVr", "emThdVy", "emThdVb", "emFrequency", "emFrequencyDeviation",
            "pressure", "soundLevel", "micHarmonics",
        ],
    )
    payload.update(overrides)
    return TelemetryFrame(**payload)


def test_idle_packet_is_not_operating():
    assert is_operating(_idle()) is False
    assert is_process_running(_idle()) is False
    assert is_operating(_idle(emIr=8.2, emPower=6.1, emMachineLoad=40.0)) is True


def test_loaded_process_is_operating_even_when_cts_read_zero():
    running = _idle(pressure=6.55, tempMotor=49.0, soundLevel=4.7, emIr=0.0, emPower=0.0, emMachineLoad=0.0)
    assert is_process_running(running) is True
    assert is_operating(running) is True
    assert three_phase_kw(running) == 0.0


def test_ct_scale_load_percent_is_not_electrical_load():
    from src.models.live_cues import is_electrically_loaded

    ghost = _idle(emMachineLoad=88.0, emIr=0.0, emIy=0.0, emIb=0.0, emPower=0.0)
    assert is_electrically_loaded(ghost) is False
    assert is_electrically_loaded(_idle(emIr=4.4, emPower=2.4, emMachineLoad=88.0)) is True


def test_max_thd_uses_all_three_phases():
    assert max_thd_pct(_idle()) == 2.1
    assert max_thd_pct(_idle(emThdVr=2.1, emThdVy=9.4, emThdVb=2.0)) == 9.4


def test_acoustic_two_x_matches_live_f1():
    peak = acoustic_two_x(_idle())
    assert peak is not None
    assert abs(peak.frequency - 93.7) < 0.01
