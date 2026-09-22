"""Isolation Forest baseline and Gbotz telemetry mapping."""

from src.config import settings
from src.mlops.fit_isolation_forest import fit_isolation_forest, is_running_healthy, row_to_vector
from src.mlops.gbotz_backfill import backfill_gbotz_history
from src.models.anomaly_detector import AnomalyDetectorEngine
from src.services.gbotz_client import gbotz_row_to_frame


def test_gbotz_nested_row_maps_to_frame():
    frame = gbotz_row_to_frame({
        "machineId": "equipment_1",
        "timestamp": 1757400000000,
        "imuAcceleration": 1.82,
        "rpm": 1480,
        "humidity": 44.0,
        "temperature": {"motor": 48.5, "compressor": 52.0},
        "energyMeter": {
            "Ir": 14.2, "Iy": 14.1, "Ib": 14.0,
            "Vr": 400.0, "Vy": 400.0, "Vb": 399.0,
            "machineLoad": 78.5,
            "voltageImbalance": 1.2,
            "power": 28.4,
        },
        "runtime": {"machineRunHours": 1420.5},
        "sensorStatus": {"ok": True, "message": "ok"},
    })
    assert frame is not None
    assert frame.machine_id == "equipment_1"
    assert frame.imu_acceleration == 1.82
    assert frame.temp_motor == 48.5
    assert frame.em_machine_load == 78.5
    assert frame.rpm == 1480


def test_gbotz_dot_path_row_maps():
    frame = gbotz_row_to_frame({
        "machineId": "equipment_1",
        "timestamp": "2026-09-09T07:00:00Z",
        "imuAcceleration": 1.5,
        "rpm": 1475,
        "temperature.motor": 41.0,
        "temperature.compressor": 42.0,
        "energyMeter.machineLoad": 70.0,
        "energyMeter.voltageImbalance": 0.9,
        "energyMeter.Ir": 10.0,
        "energyMeter.Iy": 10.0,
        "energyMeter.Ib": 10.0,
        "energyMeter.Vr": 400.0,
        "energyMeter.Vy": 400.0,
        "energyMeter.Vb": 400.0,
        "energyMeter.power": 18.0,
    })
    assert frame is not None
    assert frame.temp_motor == 41.0
    assert frame.em_voltage_imbalance == 0.9


def test_idle_row_is_not_running_healthy():
    assert not is_running_healthy({"rpm": 0, "imuAcceleration": 0.2, "emMachineLoad": 0, "emVoltageImbalance": 1.0})
    assert is_running_healthy({
        "rpm": 1480,
        "imuAcceleration": 1.8,
        "emMachineLoad": 70,
        "emVoltageImbalance": 1.0,
        "tempMotor": 48,
        "tempAmbient": 25,
        "humidity": 45,
        "sensorOk": True,
    })


def test_heuristic_score_used_before_fit(monkeypatch):
    monkeypatch.setattr(AnomalyDetectorEngine, "_try_load", lambda self: None)
    engine = AnomalyDetectorEngine()
    engine.model = None
    out = engine.compute_anomaly_score(1.5, 40.0, 25.0, 0.8, 45.0, load_pct=70.0)
    assert out["isolation_forest_fitted"] is False
    assert out["method"] == "weighted_heuristic"
    assert "anomaly_score" in out


def test_isolation_forest_fit_on_healthy_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TRAINING_DATA_DIR", str(tmp_path))
    healthy = [
        {
            "machineId": "m1",
            "rpm": 1480,
            "imuAcceleration": 1.5 + (i % 5) * 0.05,
            "tempMotor": 45.0,
            "tempAmbient": 25.0,
            "humidity": 45.0,
            "emVoltageImbalance": 0.9,
            "emMachineLoad": 70.0,
            "sensorOk": True,
        }
        for i in range(60)
    ]
    monkeypatch.setattr(
        "src.mlops.fit_isolation_forest.fetch_running_healthy_rows",
        lambda limit=20000: healthy,
    )
    result = fit_isolation_forest(experimental=True)
    assert result["ok"] is True
    assert (tmp_path / "registry" / "isolation_forest.joblib").exists()
    engine = AnomalyDetectorEngine()
    scored = engine.compute_anomaly_score(1.55, 45.0, 25.0, 0.9, 45.0, load_pct=70.0)
    assert scored["isolation_forest_fitted"] is True
    assert scored["isolation_forest_status"] in {"BASELINE_HEALTHY", "ANOMALY_DETECTED"}


def test_backfill_does_not_call_api_or_write_db_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "GBOTZ_BACKFILL_WRITE_DB", False)
    monkeypatch.setattr(settings, "AI_SERVICE_API_KEY", "should-not-be-used")
    result = backfill_gbotz_history()
    assert result["ok"] is False
    assert result.get("written") is False
    assert "GBOTZ_BACKFILL_WRITE_DB" in result["reason"]


def test_backfill_refuses_empty_api_key_when_write_enabled(monkeypatch):
    monkeypatch.setattr(settings, "GBOTZ_BACKFILL_WRITE_DB", True)
    monkeypatch.setattr(settings, "AI_SERVICE_API_KEY", "")
    result = backfill_gbotz_history()
    assert result["ok"] is False
    assert "AI_SERVICE_API_KEY" in result["reason"]


def test_row_to_vector_length():
    vec = row_to_vector({
        "imuAcceleration": 1.8,
        "tempMotor": 48,
        "tempAmbient": 25,
        "emVoltageImbalance": 1.1,
        "emMachineLoad": 72,
        "humidity": 40,
    })
    assert len(vec) == 5
    assert abs(vec[1] - 23.0) < 1e-6
