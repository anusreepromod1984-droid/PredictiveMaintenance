from src.agents.agent_delta import AgentDelta
from src.agents.open_wo_tracker import OpenWorkOrderTracker
from src.models.oem_mail import compose_oem_mail
from src.models.supplier_sourcing_engine import SupplierSourcingEngine
from src.schemas.predictions import (
    CableCheckStatus,
    CMMSWorkOrder,
    DefectLocalization,
    RULPrediction,
)
from src.schemas.telemetry import TelemetryFrame


def _rul(days=2.0, due="2026-09-12"):
    return RULPrediction(
        rul_operating_hours=days * 24,
        rul_days=days,
        confidence_interval_bounds="B10/B50",
        recommended_repair_by_date=due,
    )


def test_oem_mail_names_issue_part_due_and_buy_links_when_bin_empty():
    intel = SupplierSourcingEngine().evaluate_sourcing(
        "Compressed-air circuit / fittings / drain traps",
        rul_days=2,
        defect_code="PF001",
    )
    wo = CMMSWorkOrder(
        work_order_id="4811514",
        ticket_type="PM_CONDITION",
        scheduled_repair_window="Fri 11 Sep 22:00–06:00 · Night A",
        pm_due_date="2026-09-12",
        reserved_spare_part_bom="Festo QSL-1/4-8",
        reserved_warehouse_bin="BIN-AIR-02",
        sourcing_intelligence=intel,
    )
    defect = DefectLocalization(
        defect_code="PF001",
        defect_name="Compressed-air leak",
        failing_component="Compressed-air circuit / fittings / drain traps",
        confidence_percentage=90.0,
    )
    draft = compose_oem_mail("compressor_unit_01", defect, _rul(), wo)
    assert "PF001" in draft.subject
    assert "Festo QSL-1/4-8" in draft.subject
    assert "not in warehouse" in draft.subject.lower()
    assert "2026-09-12" in draft.subject
    assert "Compressed-air leak" in draft.message
    assert "Not in warehouse" in draft.message
    assert "Moglix" in draft.message
    assert "msn2r9dpkvxymf" in draft.message
    assert "<html" not in draft.message.lower()
    assert "<p>" not in draft.message
    assert draft.message.startswith("Hi,")
    assert "Thank you." in draft.message
    assert draft.warehouse_in_stock is False
    assert draft.part_number == "Festo QSL-1/4-8"


def test_oem_mail_says_pick_from_stores_when_bin_has_stock():
    intel = SupplierSourcingEngine().evaluate_sourcing("SKF-6310", rul_days=20)
    wo = CMMSWorkOrder(
        work_order_id="4000001",
        ticket_type="PDM_CORRECTIVE",
        scheduled_repair_window="next window",
        pm_due_date="2026-09-20",
        reserved_spare_part_bom="SKF-6310 ×1",
        reserved_warehouse_bin="BIN-B07",
        sourcing_intelligence=intel,
    )
    defect = DefectLocalization(
        defect_code="BPFI",
        defect_name="Inner-race bearing",
        failing_component="SKF-6310",
        confidence_percentage=88.0,
    )
    draft = compose_oem_mail("compressor_unit_01", defect, _rul(20, "2026-09-20"), wo)
    assert "in BIN-B07" in draft.subject or "in warehouse" in draft.subject.lower()
    assert "In warehouse" in draft.message
    assert "Moglix" not in draft.message
    assert draft.warehouse_in_stock is True


def test_compose_from_stale_work_order_record():
    intel = SupplierSourcingEngine().evaluate_sourcing(
        "Compressed-air circuit / fittings / drain traps",
        rul_days=2,
        defect_code="PF001",
    )
    from src.models.oem_mail import compose_oem_mail_from_record

    draft = compose_oem_mail_from_record({
        "machine_id": "compressor_unit_01",
        "defect_code": "PF001",
        "defect_name": "Compressed-air leak",
        "work_order_id": "4811514",
        "ticket_type": "PM_CONDITION",
        "scheduled_repair_window": "Night A",
        "pm_due_date": "2026-09-12",
        "reserved_spare_part_bom": "Festo QSL-1/4-8",
        "reserved_warehouse_bin": "BIN-AIR-02",
        "sourcing_intelligence": intel.model_dump(mode="json"),
    })
    assert "PF001" in draft.subject
    assert "not in warehouse" in draft.subject.lower()
    assert "Festo QSL-1/4-8" in draft.subject


def test_delta_attaches_oem_mail_draft():
    from src.services import oem_mailer

    oem_mailer.note_machine_cleared("mail_draft_unit")
    tracker = OpenWorkOrderTracker()
    tracker.clear_open_work_order("mail_draft_unit")
    frame = TelemetryFrame(
        machineId="mail_draft_unit",
        imuAcceleration=8.5,
        rpm=1480,
        tempMotor=74.0,
        tempAmbient=25.0,
        emIr=20.0,
        emPower=12.0,
        bearingModel="SKF-6208",
    )
    defect = DefectLocalization(
        defect_code="BPFI",
        defect_name="Inner-race bearing",
        failing_component="SKF-6208",
        confidence_percentage=90.0,
    )
    wo = AgentDelta(tracker=tracker).process(
        frame, defect, _rul(7, "2026-09-18"), cable_check=CableCheckStatus(status="VALID", alert_suppressed=False)
    )
    assert wo is not None
    assert wo.oem_mail is not None
    assert "BPFI" in wo.oem_mail.subject
    assert wo.oem_mail.message
    assert wo.oem_mail.sent is False


def test_delta_auto_sends_oem_mail_once(monkeypatch):
    from src.services import oem_mailer

    sent = []

    def fake_send(draft):
        sent.append(draft.subject)
        return {"to": "anusree.p@greenbotz.co", "cc": "", "status_code": 200, "body": {}}

    monkeypatch.setattr(oem_mailer, "mail_configured", lambda: True)
    monkeypatch.setattr(oem_mailer, "send_oem_mail", fake_send)
    oem_mailer.note_machine_cleared("mail_auto_unit")

    tracker = OpenWorkOrderTracker()
    tracker.clear_open_work_order("mail_auto_unit")
    frame = TelemetryFrame(
        machineId="mail_auto_unit",
        imuAcceleration=8.5,
        rpm=1480,
        tempMotor=74.0,
        tempAmbient=25.0,
        emIr=20.0,
        emPower=12.0,
        bearingModel="SKF-6208",
    )
    defect = DefectLocalization(
        defect_code="BPFI",
        defect_name="Inner-race bearing",
        failing_component="SKF-6208",
        confidence_percentage=90.0,
    )
    delta = AgentDelta(tracker=tracker)
    first = delta.process(frame, defect, _rul(7, "2026-09-18"), cable_check=CableCheckStatus(status="VALID", alert_suppressed=False))
    second = delta.process(frame, defect, _rul(7, "2026-09-18"), cable_check=CableCheckStatus(status="VALID", alert_suppressed=False))
    assert first is not None and first.oem_mail is not None
    assert first.oem_mail.sent is True
    assert second is not None and second.oem_mail is not None
    assert second.oem_mail.sent is True
    assert len(sent) == 1


def test_delta_mails_again_when_a_different_error_needs_a_part(monkeypatch):
    from src.services import oem_mailer

    sent = []
    monkeypatch.setattr(oem_mailer, "mail_configured", lambda: True)
    monkeypatch.setattr(oem_mailer, "send_oem_mail", lambda draft: sent.append(draft.subject) or {"to": "x", "cc": ""})
    oem_mailer.note_machine_cleared("mail_auto_unit")

    tracker = OpenWorkOrderTracker()
    tracker.clear_open_work_order("mail_auto_unit")
    frame = TelemetryFrame(
        machineId="mail_auto_unit",
        imuAcceleration=8.5,
        rpm=1480,
        tempMotor=74.0,
        tempAmbient=25.0,
        emIr=20.0,
        emPower=12.0,
        bearingModel="SKF-6208",
    )
    cable = CableCheckStatus(status="VALID", alert_suppressed=False)
    delta = AgentDelta(tracker=tracker)
    bpfi = DefectLocalization(
        defect_code="BPFI", defect_name="Inner-race bearing", failing_component="SKF-6208", confidence_percentage=90.0
    )
    mf002 = DefectLocalization(
        defect_code="MF002", defect_name="Misalignment", failing_component="Lovejoy L-100", confidence_percentage=90.0
    )
    delta.process(frame, bpfi, _rul(7, "2026-09-18"), cable_check=cable)
    delta.process(frame, mf002, _rul(14, "2026-09-30"), cable_check=cable)
    assert len(sent) == 2
    delta.process(frame, mf002, _rul(14, "2026-09-30"), cable_check=cable)
    assert len(sent) == 2
    delta.process(frame, bpfi, _rul(7, "2026-09-18"), cable_check=cable)
    assert len(sent) == 2


def test_normal_vibration_skip_does_not_mail(monkeypatch):
    from src.services import oem_mailer

    sent = []
    monkeypatch.setattr(oem_mailer, "mail_configured", lambda: True)
    monkeypatch.setattr(oem_mailer, "send_oem_mail", lambda draft: sent.append(draft.subject) or {"to": "x", "cc": ""})
    oem_mailer.note_machine_cleared("mail_skip_unit")

    tracker = OpenWorkOrderTracker()
    tracker.clear_open_work_order("mail_skip_unit")
    frame = TelemetryFrame(
        machineId="mail_skip_unit",
        imuAcceleration=0.0,
        rpm=1480,
        tempMotor=49.0,
        tempAmbient=25.0,
        emIr=14.5,
        emPower=8.6,
        bearingModel="SKF-6208",
    )
    defect = DefectLocalization(
        defect_code="NORMAL",
        defect_name="Vibration prediction skipped — IMU-Acceleration not on this packet",
        failing_component="Vibration transducer not publishing",
        confidence_percentage=0.0,
        diagnosis_method="imu_absent",
    )
    wo = AgentDelta(tracker=tracker).process(
        frame, defect, _rul(40.8, "2026-10-22"), cable_check=CableCheckStatus(status="VALID", alert_suppressed=False)
    )
    assert wo is None
    assert sent == []


def test_prior_sent_does_not_mail_again(monkeypatch):
    from src.services import oem_mailer
    from src.models.oem_mail import compose_oem_mail

    sent = []
    monkeypatch.setattr(oem_mailer, "mail_configured", lambda: True)
    monkeypatch.setattr(oem_mailer, "send_oem_mail", lambda draft: sent.append(draft.subject) or {"to": "x", "cc": ""})
    oem_mailer.note_machine_cleared("mail_prior_unit")

    intel = SupplierSourcingEngine().evaluate_sourcing("SKF-6208", rul_days=7, defect_code="BPFI")
    wo = CMMSWorkOrder(
        work_order_id="4000001",
        ticket_type="PDM_CORRECTIVE",
        scheduled_repair_window="next window",
        pm_due_date="2026-09-18",
        reserved_spare_part_bom="SKF-6208 ×1",
        reserved_warehouse_bin="BIN-A12",
        sourcing_intelligence=intel,
    )
    defect = DefectLocalization(
        defect_code="BPFI",
        defect_name="Inner-race bearing",
        failing_component="SKF-6208",
        confidence_percentage=90.0,
    )
    draft = compose_oem_mail("mail_prior_unit", defect, _rul(7, "2026-09-18"), wo)
    oem_mailer.dispatch_if_needed(
        draft,
        machine_id="mail_prior_unit",
        defect_code="BPFI",
        prior={"sent": True, "sent_at": "2026-09-11T13:00:00+00:00"},
    )
    assert sent == []


def test_mqtt_ticks_do_not_mail_diagnose_sends_once(monkeypatch):
    from src.services import oem_mailer

    sent = []
    monkeypatch.setattr(oem_mailer, "mail_configured", lambda: True)
    monkeypatch.setattr(oem_mailer, "send_oem_mail", lambda draft: sent.append(draft.subject) or {"to": "x", "cc": ""})
    oem_mailer.note_machine_cleared("mail_mqtt_unit")

    tracker = OpenWorkOrderTracker()
    tracker.clear_open_work_order("mail_mqtt_unit")
    frame = TelemetryFrame(
        machineId="mail_mqtt_unit",
        imuAcceleration=6.2,
        rpm=1480,
        tempMotor=49.0,
        tempAmbient=25.0,
        emIr=14.5,
        emPower=8.6,
        bearingModel="SKF-6208",
    )
    defect = DefectLocalization(
        defect_code="MF002",
        defect_name="Shaft Angular & Parallel Misalignment",
        failing_component="Lovejoy L-100",
        confidence_percentage=90.0,
    )
    cable = CableCheckStatus(status="VALID", alert_suppressed=False)
    mqtt_delta = AgentDelta(tracker=tracker)
    mqtt_delta.notify_oem = False
    mqtt_delta.process(frame, defect, _rul(14, "2026-09-30"), cable_check=cable)
    mqtt_delta.process(frame, defect, _rul(14, "2026-09-30"), cable_check=cable)
    assert sent == []

    diagnose = AgentDelta(tracker=tracker)
    diagnose.notify_oem = True
    diagnose.process(frame, defect, _rul(14, "2026-09-30"), cable_check=cable)
    diagnose.process(frame, defect, _rul(14, "2026-09-30"), cable_check=cable)
    assert len(sent) == 1
