"""API security: locked CORS defaults, optional API key, readiness."""

from fastapi.testclient import TestClient
from src.api.main import app
from src.config import settings


client = TestClient(app)


def test_ready_endpoint_exists():
    response = client.get("/api/v1/ready")
    assert response.status_code in (200, 503)
    assert response.json()["status"] in ("READY", "NOT_READY")


def test_public_config_reports_auth_flag():
    response = client.get("/api/v1/config/public")
    assert response.status_code == 200
    body = response.json()
    assert "auth_required" in body
    assert body["api_key_header"] == "X-API-Key"


def test_predict_requires_api_key_when_enabled(monkeypatch):
    original_auth = settings.AUTH_ENABLED
    original_key = settings.APMS_API_KEY
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "APMS_API_KEY", "plant-test-key")
    try:
        denied = client.post("/api/v1/predict_rul", json={
            "machineId": "compressor_unit_01",
            "imuAcceleration": 1.2,
            "tempMotor": 40.0,
            "tempCompressor": 41.0,
            "emIr": 10.0, "emIy": 10.0, "emIb": 10.0,
            "emVr": 400.0, "emVy": 400.0, "emVb": 400.0,
            "emMachineLoad": 60.0,
            "emVoltageImbalance": 0.8,
            "emPower": 6.37,
        })
        assert denied.status_code == 401

        allowed = client.post(
            "/api/v1/predict_rul",
            headers={"X-API-Key": "plant-test-key"},
            json={
                "machineId": "compressor_unit_01",
                "imuAcceleration": 1.2,
                "tempMotor": 40.0,
                "tempCompressor": 41.0,
                "emIr": 10.0, "emIy": 10.0, "emIb": 10.0,
                "emVr": 400.0, "emVy": 400.0, "emVb": 400.0,
                "emMachineLoad": 60.0,
                "emVoltageImbalance": 0.8,
                "emPower": 6.37,
            },
        )
        assert allowed.status_code == 200
    finally:
        settings.AUTH_ENABLED = original_auth
        settings.APMS_API_KEY = original_key


def test_health_stays_open_when_auth_enabled(monkeypatch):
    original_auth = settings.AUTH_ENABLED
    original_key = settings.APMS_API_KEY
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "APMS_API_KEY", "plant-test-key")
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert "fitted_artifacts_loaded" in response.json()
    finally:
        settings.AUTH_ENABLED = original_auth
        settings.APMS_API_KEY = original_key


def test_cors_parser_locks_to_explicit_origins():
    from src.config import AppSettings
    locked = AppSettings(CORS_ORIGINS="http://127.0.0.1:8000,http://localhost:8000")
    origins = locked.cors_origin_list()
    assert "*" not in origins
    assert "http://127.0.0.1:8000" in origins


def test_psycopg_url_strips_prisma_schema():
    from src.database.connection import _psycopg_url
    stripped = _psycopg_url("postgresql://pdm:pdm@localhost:5432/pdm_dev?schema=public")
    assert "schema=" not in stripped
    assert stripped.startswith("postgresql://")
