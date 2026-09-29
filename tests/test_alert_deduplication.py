import concurrent.futures
from datetime import datetime, timezone
import pytest

from src.schemas.predictions import (
    PredictRULResponse,
    DefectLocalization,
    RULPrediction,
    CableCheckStatus,
)
from src.schemas.telemetry import TelemetryFrame
from src.services import alert_dispatcher


def _make_dummy_response(machine_id: str, defect_code: str = "BPFI", severity_status: str = "DEGRADED"):
    return PredictRULResponse(
        machine_id=machine_id,
        trace_id="test_trc_001",
        timestamp=datetime.now(timezone.utc),
        overall_health_score=45.0,
        overall_health_status=severity_status,
        iso_vibration_zone="C",
        cable_check=CableCheckStatus(status="VALID", alert_suppressed=False, fault_reason=None),
        defect_localization=DefectLocalization(
            defect_code=defect_code,
            defect_name="Bearing Inner Race Defect",
            failing_component="Drive-End Bearing",
            confidence_percentage=95.0,
        ),
        rul_prediction=RULPrediction(
            rul_days=25.0,
            rul_operating_hours=600.0,
            confidence_interval_bounds="[20.0, 30.0]",
            recommended_repair_by_date="2026-10-15",
        ),
    )


def _make_dummy_frame(machine_id: str):
    return TelemetryFrame(
        machineId=machine_id,
        imuAcceleration=5.5,
        tempMotor=78.0,
        tempCompressor=65.0,
    )


@pytest.fixture(autouse=True)
def clean_cooldown():
    """Ensure clean cooldown before and after each test."""
    alert_dispatcher.note_alert_cleared("test_unit_99", clear_cooldown=True)
    yield
    alert_dispatcher.note_alert_cleared("test_unit_99", clear_cooldown=True)


def test_single_alert_and_subsequent_debounce(monkeypatch):
    sent_whatsapp = []
    sent_emails = []

    monkeypatch.setattr(alert_dispatcher, "send_whatsapp_alert", lambda txt: sent_whatsapp.append(txt) or {"success": True})
    monkeypatch.setattr(alert_dispatcher, "send_email_alert", lambda subj, html: sent_emails.append(subj) or {"success": True})

    machine_id = "test_unit_99"
    pred = _make_dummy_response(machine_id, defect_code="BPFI")
    frame = _make_dummy_frame(machine_id)

    # First call: Should dispatch
    res1 = alert_dispatcher.dispatch_alert_notifications(machine_id, pred, frame)
    assert res1["dispatched"] is True
    assert len(sent_whatsapp) == 1
    assert len(sent_emails) == 1

    # Second call immediate: Must be rejected on cooldown
    res2 = alert_dispatcher.dispatch_alert_notifications(machine_id, pred, frame)
    assert res2["dispatched"] is False
    assert "cooldown" in res2["reason"].lower()
    # No additional messages dispatched
    assert len(sent_whatsapp) == 1
    assert len(sent_emails) == 1


def test_concurrent_burst_only_triggers_one_alert(monkeypatch):
    sent_whatsapp = []
    sent_emails = []

    def mock_send_wa(txt):
        import time
        time.sleep(0.05)  # Simulate network HTTP latency
        sent_whatsapp.append(txt)
        return {"success": True}

    def mock_send_mail(subj, html):
        import time
        time.sleep(0.05)  # Simulate network HTTP latency
        sent_emails.append(subj)
        return {"success": True}

    monkeypatch.setattr(alert_dispatcher, "send_whatsapp_alert", mock_send_wa)
    monkeypatch.setattr(alert_dispatcher, "send_email_alert", mock_send_mail)

    machine_id = "test_unit_99"
    pred = _make_dummy_response(machine_id, defect_code="BPFI")
    frame = _make_dummy_frame(machine_id)

    # Fire 10 parallel threads at the exact same moment
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(alert_dispatcher.dispatch_alert_notifications, machine_id, pred, frame)
            for _ in range(10)
        ]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    dispatched = [r for r in results if r["dispatched"] is True]
    rejected = [r for r in results if r["dispatched"] is False]

    # Exactly 1 must have dispatched, 9 must have been rejected
    assert len(dispatched) == 1
    assert len(rejected) == 9
    assert len(sent_whatsapp) == 1
    assert len(sent_emails) == 1


def test_machine_level_anti_oscillation_debounce(monkeypatch):
    sent_whatsapp = []
    sent_emails = []

    monkeypatch.setattr(alert_dispatcher, "send_whatsapp_alert", lambda txt: sent_whatsapp.append(txt) or {"success": True})
    monkeypatch.setattr(alert_dispatcher, "send_email_alert", lambda subj, html: sent_emails.append(subj) or {"success": True})

    machine_id = "test_unit_99"
    pred_flatline = _make_dummy_response(machine_id, defect_code="SENSOR_FROZEN_FLATLINE")
    pred_bpfi = _make_dummy_response(machine_id, defect_code="BPFI")
    frame = _make_dummy_frame(machine_id)

    # 1. First alert for Flatline
    res1 = alert_dispatcher.dispatch_alert_notifications(machine_id, pred_flatline, frame)
    assert res1["dispatched"] is True
    assert len(sent_whatsapp) == 1

    # 2. Immediately after, an alert for BPFI on the same machine arrives
    # Anti-storm machine debounce must prevent it from firing
    res2 = alert_dispatcher.dispatch_alert_notifications(machine_id, pred_bpfi, frame)
    assert res2["dispatched"] is False
    assert "debounce" in res2["reason"].lower() or "cooldown" in res2["reason"].lower()
    assert len(sent_whatsapp) == 1


def test_routine_clear_does_not_wipe_cooldown(monkeypatch):
    sent_whatsapp = []
    sent_emails = []

    monkeypatch.setattr(alert_dispatcher, "send_whatsapp_alert", lambda txt: sent_whatsapp.append(txt) or {"success": True})
    monkeypatch.setattr(alert_dispatcher, "send_email_alert", lambda subj, html: sent_emails.append(subj) or {"success": True})

    machine_id = "test_unit_99"
    pred = _make_dummy_response(machine_id, defect_code="BPFI")
    frame = _make_dummy_frame(machine_id)

    # Dispatch alert
    res1 = alert_dispatcher.dispatch_alert_notifications(machine_id, pred, frame)
    assert res1["dispatched"] is True
    assert len(sent_whatsapp) == 1

    # Simulated healthy frame / routine tick calling clear without clear_cooldown=True
    alert_dispatcher.note_alert_cleared(machine_id, clear_cooldown=False)

    # Subsequent alert: Must STILL be blocked by cooldown!
    res2 = alert_dispatcher.dispatch_alert_notifications(machine_id, pred, frame)
    assert res2["dispatched"] is False
    assert len(sent_whatsapp) == 1

    # Explicit operator reset with clear_cooldown=True
    alert_dispatcher.note_alert_cleared(machine_id, clear_cooldown=True)

    # Now it can fire
    res3 = alert_dispatcher.dispatch_alert_notifications(machine_id, pred, frame)
    assert res3["dispatched"] is True
    assert len(sent_whatsapp) == 2
