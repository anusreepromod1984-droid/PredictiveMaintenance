"""ISO 20816-3 zone mapping for 37 kW class machines."""

from src.models.iso_20816 import classify_velocity_zone, zone_health_score, zone_limits


def test_group2_flexible_matches_legacy_danger_7_1():
    ab, bc, cd = zone_limits("2", "flexible")
    assert (ab, bc, cd) == (2.3, 4.5, 7.1)
    assert classify_velocity_zone(1.2, "2", "flexible") == "A"
    assert classify_velocity_zone(3.0, "2", "flexible") == "B"
    assert classify_velocity_zone(5.2, "2", "flexible") == "C"
    assert classify_velocity_zone(7.9, "2", "flexible") == "D"


def test_iso_uses_overall_rms_when_axes_are_not_the_same_quantity():
    from src.models.iso_20816 import evaluation_velocity_rms
    from src.schemas.telemetry import TelemetryFrame

    frame = TelemetryFrame(
        machineId="compressor_unit_01",
        imuAcceleration=0.12,
        xAxisVibration=19.87,
        yAxisVibration=4.99,
        zAxisVibration=9.47,
        sourceKeys=["imuAcceleration", "xAxisVibration", "yAxisVibration", "zAxisVibration"],
    )
    assert abs(evaluation_velocity_rms(frame) - 0.12) < 1e-9


def test_health_score_drops_in_zone_d():
    healthy = zone_health_score(1.2, "2", "flexible")
    trip = zone_health_score(7.9, "2", "flexible")
    assert healthy >= 88.0
    assert trip <= 40.0
