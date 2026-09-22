"""Training inbox, fit gates, and inference artifact loaders."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.config import settings
from src.mlops.fit_classifier import fit_classifier
from src.mlops.fit_weibull import fit_weibull
from src.mlops.status import dataset_status
from src.mlops.store import add_labeled_window, add_life_event, import_life_events_csv
from src.schemas.training import ComponentLifeEvent, LabeledWindow

client = TestClient(app)


@pytest.fixture
def training_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TRAINING_DATA_DIR", str(tmp_path))
    return tmp_path


def test_training_status_lists_what_plant_must_provide(training_dir):
    response = client.get("/api/v1/training/status")
    assert response.status_code == 200
    data = response.json()
    assert data["weibull"]["n_failures"] == 0
    assert data["classifier"]["n_windows"] == 0
    assert data["pinn"]["n_trajectories"] == 0
    assert "baseline" in data
    assert any("component_life_events" in item for item in data["you_must_provide"])
    assert any("labeled_windows" in item for item in data["you_must_provide"])
    assert any("rul_trajectories" in item for item in data["you_must_provide"])


def test_example_csv_rows_are_skipped(training_dir):
    template = Path("data/training/templates/component_life_events.csv")
    imported = import_life_events_csv(template)
    assert imported == 0
    assert dataset_status()["weibull"]["n_failures"] == 0


def test_weibull_fit_with_eight_failures(training_dir):
    for i in range(8):
        add_life_event(ComponentLifeEvent(
            machineId=f"plant_comp_{i:02d}",
            component="DE_bearing",
            assetClass="SKF-6208",
            hoursAtInstall=0,
            hoursAtEvent=7000 + i * 250,
            eventType="failed",
        ))
    add_life_event(ComponentLifeEvent(
        machineId="plant_comp_live",
        component="DE_bearing",
        assetClass="SKF-6208",
        hoursAtInstall=0,
        hoursAtEvent=4300,
        eventType="still_running",
    ))
    result = fit_weibull(asset_class="SKF-6208")
    assert result["ok"] is True
    assert result["n_failures"] == 8
    assert result["beta"] > 0
    assert result["eta"] > 0
    assert (training_dir / "registry" / "weibull.json").exists()


def test_classifier_fit_rejects_too_few_labels(training_dir):
    add_labeled_window(LabeledWindow(
        machineId="plant_comp_01",
        timestamp="now",
        label="NORMAL",
        features={"imuAcceleration": 1.4},
    ))
    result = fit_classifier()
    assert result["ok"] is False
    response = client.post("/api/v1/training/fit/classifier", json={"experimental": False})
    assert response.status_code == 400


def test_classifier_experimental_fit(training_dir, monkeypatch):
    monkeypatch.setattr(settings, "MIN_CLASSIFIER_PER_CLASS", 4)
    for i in range(4):
        add_labeled_window(LabeledWindow(
            machineId=f"plant_n_{i}",
            timestamp="now",
            label="NORMAL",
            features={
                "imuAcceleration": 1.2 + i * 0.1,
                "emVoltageImbalance": 0.8,
                "emMachineLoad": 70,
                "tempMotor": 40,
                "tempAmbient": 28,
                "pattern": "STABLE",
                "isolatedDomain": "MECHANICAL",
                "matchedFault": "NONE",
            },
        ))
        add_labeled_window(LabeledWindow(
            machineId=f"plant_b_{i}",
            timestamp="now",
            label="BPFI",
            features={
                "imuAcceleration": 6.0 + i * 0.2,
                "emVoltageImbalance": 1.1,
                "emMachineLoad": 82,
                "tempMotor": 55,
                "tempAmbient": 29,
                "pattern": "STEADY_CLIMB",
                "isolatedDomain": "MECHANICAL",
                "matchedFault": "BPFI",
            },
        ))
    result = fit_classifier(experimental=True)
    assert result["ok"] is True
    assert (training_dir / "registry" / "classifier.ubj").exists()


def test_maintenance_complete_records_life_event(training_dir):
    response = client.post("/api/v1/maintenance_complete", json={
        "machineId": "compressor_unit_01",
        "hoursAtEvent": 8420,
        "eventType": "failed",
        "component": "DE_bearing",
        "assetClass": "SKF-6208",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["buffer_reset"] is True
    assert body["life_event_recorded"] is True
    inbox = training_dir / "inbox" / "component_life_events.jsonl"
    assert inbox.exists()
    assert "compressor_unit_01" in inbox.read_text(encoding="utf-8")


def test_maintenance_complete_without_hours_does_not_record():
    response = client.post("/api/v1/maintenance_complete", json={"machineId": "compressor_unit_01"})
    assert response.status_code == 200
    assert response.json()["life_event_recorded"] is False
