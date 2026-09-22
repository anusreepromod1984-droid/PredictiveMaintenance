from fastapi.testclient import TestClient

from src.api.main import app
from src.agents.open_wo_tracker import wo_tracker
from src.schemas.predictions import OemMailDraft


client = TestClient(app)


def test_oem_mail_send_uses_agent_draft(monkeypatch):
    draft = OemMailDraft(
        subject="PF001 Compressed-air leak — Festo QSL-1/4-8 required by 2026-09-12 (not in warehouse)",
        message="<p>test</p>",
        preview="Festo QSL-1/4-8 is not in the warehouse.",
        warehouse_in_stock=False,
        required_by="2026-09-12",
        part_number="Festo QSL-1/4-8",
    )
    wo_tracker.register_open_work_order(
        "compressor_unit_01",
        "PF001",
        {
            "work_order_id": "4811514",
            "ticket_type": "PM_CONDITION",
            "oem_mail": draft.model_dump(),
        },
    )
    monkeypatch.setattr("src.api.routes.oem_mail.mail_configured", lambda: True)
    monkeypatch.setattr(
        "src.api.routes.oem_mail.send_oem_mail",
        lambda _draft: {"to": "anusree.p@greenbotz.co", "cc": "", "status_code": 200, "body": {"ok": True}},
    )
    response = client.post(
        "/api/v1/oem-mail/send",
        json={"machine_id": "compressor_unit_01", "defect_code": "PF001"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["to"] == "anusree.p@greenbotz.co"
    assert body["subject"].startswith("PF001")
    stored = wo_tracker.has_open_work_order("compressor_unit_01", "PF001")
    assert stored["oem_mail"]["sent"] is True


def test_oem_mail_send_requires_open_wo(monkeypatch):
    monkeypatch.setattr("src.api.routes.oem_mail.mail_configured", lambda: True)
    response = client.post(
        "/api/v1/oem-mail/send",
        json={"machine_id": "missing_machine", "defect_code": "PF001"},
    )
    assert response.status_code == 404
