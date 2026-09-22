from datetime import datetime
from zoneinfo import ZoneInfo

from src.models.pdm_program import build_watchlist, standing_pm, watch_score, watch_status
from src.models.shift_calendar import next_night_window, next_planned_outage, next_repair_window
from src.models.fleet_registry import criticality_for, list_registered_assets


IST = ZoneInfo("Asia/Kolkata")
FRIDAY = datetime(2026, 9, 11, 15, 45, tzinfo=IST)


def test_fleet_registry_has_three_assets():
    ids = [row["machine_id"] for row in list_registered_assets()]
    assert "compressor_unit_01" in ids
    assert criticality_for("compressor_unit_01") == 5


def test_watch_score_ranks_short_rul_and_criticality_first():
    leak = watch_score(5, 2.0, "PF001", False)
    overdue_pump = watch_score(4, 40.0, "NORMAL", True)
    healthy_motor = watch_score(3, 40.0, "NORMAL", False)
    assert leak > overdue_pump > healthy_motor


def test_watch_status_language_is_not_fail():
    assert watch_status(2.0, "PF001", False, 2.0) == "severe"
    assert watch_status(10.0, "NORMAL", True, -2.0) == "warning"
    assert watch_status(40.0, "NORMAL", False, 12.0) == "healthy"


def test_urgent_rul_books_tonight_not_a_placeholder():
    slot = next_repair_window(2.0, "PDM_CORRECTIVE", now=FRIDAY)
    assert slot["kind"] == "night_window"
    assert "Night A" in slot["label"]
    assert "NEXT_APPROVED_CMMS_WINDOW" not in slot["label"]
    start = datetime.fromisoformat(slot["start"])
    assert start.hour == 22
    assert start.day == 11


def test_healthy_rul_books_saturday_outage():
    slot = next_repair_window(14.0, "PDM_CORRECTIVE", now=FRIDAY)
    assert slot["kind"] == "planned_outage"
    start = datetime.fromisoformat(slot["start"])
    assert start.weekday() == 5
    assert start.hour == 6


def test_watchlist_includes_registered_assets_and_pm_due():
    rows = build_watchlist(now=FRIDAY)
    by_id = {row["machine_id"]: row for row in rows}
    assert "compressor_unit_01" in by_id
    assert "chiller_pump_03" in by_id
    pump = by_id["chiller_pump_03"]
    assert pump["pm_overdue"] is True
    assert pump["pm_task"]
    assert rows[0]["watch_score"] >= rows[-1]["watch_score"]


def test_standing_pm_marks_overdue_oil_sample():
    asset = next(row for row in list_registered_assets() if row["machine_id"] == "chiller_pump_03")
    tasks = standing_pm(asset, now=FRIDAY)
    assert tasks
    assert tasks[0]["overdue"] is True


def test_rul_markers_call_then_repair():
    from src.models.pdm_program import _markers_from_points

    points = [
        {"x": 1, "rul_days": 20, "defect_code": "NORMAL"},
        {"x": 2, "rul_days": 8, "defect_code": "BPFI"},
        {"x": 3, "rul_days": 40, "defect_code": "NORMAL"},
    ]
    kinds = [row["kind"] for row in _markers_from_points(points)]
    assert kinds[0] == "call"
    assert "repair" in kinds

    noisy = [
        {"x": 1, "rul_days": 2.0, "defect_code": "PF001"},
        {"x": 2, "rul_days": 8.0, "defect_code": "PF001"},
        {"x": 3, "rul_days": 2.0, "defect_code": "PF001"},
    ]
    assert all(row["kind"] != "repair" for row in _markers_from_points(noisy))
    night = next_night_window(FRIDAY)
    sat = next_planned_outage(FRIDAY)
    assert datetime.fromisoformat(night["start"]).tzinfo is not None
    assert datetime.fromisoformat(sat["start"]).weekday() == 5
