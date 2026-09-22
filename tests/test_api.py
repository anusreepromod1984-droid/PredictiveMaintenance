"""
FastAPI REST API Route Tests
"""

import pytest
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)


def test_api_health_endpoint():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("UP", "DEGRADED")
    assert "ai_models_loaded" in data
    assert data["rul_model_version"] == "physics_weibull_v1"


def test_api_predict_rul_endpoint():
    payload = {
        "machineId": "compressor_unit_01",
        "imuAcceleration": 7.9051,
        "rpm": 1480,
        "tempMotor": 28.94,
        "tempCompressor": 29.06,
        "tempAmbient": 25.0,
        "humidity": 45.2,
        "emIr": 28.8,
        "emIy": 29.1,
        "emIb": 28.9,
        "emVr": 395.62,
        "emVy": 404.55,
        "emVb": 397.07,
        "emMachineLoad": 82.5,
        "emVoltageImbalance": 1.371,
        "emPower": 18.4,
        "soundLevel": -7.1,
        "magRoll": 0.12,
        "magPitch": 0.05,
        "magYaw": 0.01,
        "runHours": 1420.5,
        "vibrationHarmonics": [
            {"frequency": 109.4, "amplitude": -15.26},
            {"frequency": 218.8, "amplitude": -19.05}
        ]
    }

    response = client.post("/api/v1/predict_rul", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["machine_id"] == "compressor_unit_01"
    assert data["cable_check"]["status"] == "VALID"
    assert data["rul_prediction"]["rul_days"] > 0.0
    complete = client.post("/api/v1/maintenance_complete", json={"machineId": "compressor_unit_01"})
    assert complete.status_code == 200
    assert complete.json()["buffer_reset"] is True
