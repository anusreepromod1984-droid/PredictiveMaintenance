"""AI Assistant must honour the platform's vibration alert band, not only ISO zones."""

from src.agents import agent_gamma as ag
from src.agents.agent_gamma import AgentGamma
from src.agents.orchestrator import APMSOrchestrator
from src.services import api_limits
from src.services.gbotz_client import gbotz_row_to_frame


def _frame(v):
    return gbotz_row_to_frame({
        "machineId": "G-Sensor-01", "timestamp": 1791987640823, "imuAcceleration": v, "rpm": 0,
        "temperature": {"motor": 31, "compressor": 30},
        "energyMeter": {"Ir": 0, "Iy": 0, "Ib": 0, "machineLoad": 0, "power": 0},
    })


def test_parse_band_ignores_unrelated_channels():
    prof = {"parameters": [
        {"name": "Dominant Frequency 1", "parameters": [
            {"name": "Frequency 1", "min": 10, "max": 1000, "enabled": True, "alert_threshold": None}]},
        {"name": "IMU-Acceleration", "parameters": [
            {"name": "Acceleration", "min": 0, "max": 0.1, "enabled": True, "alert_threshold": 5}]},
    ]}
    assert api_limits.parse_vibration_band(prof) == (0.0, 0.1)
    assert api_limits.parse_vibration_band({"parameters": []}) is None


def test_breach_becomes_critical_defect(monkeypatch):
    monkeypatch.setattr(ag, "get_vibration_band", lambda mid: (0.0, 0.1))
    defect, _ = AgentGamma().process(_frame(0.1176))
    assert defect.diagnosis_method == "api_alert_threshold"
    assert defect.defect_code != "NORMAL"
    assert "outside alert range 0-0.1" in defect.defect_name
    orch = APMSOrchestrator.__new__(APMSOrchestrator)
    status, _card = orch._derive_status_and_card(
        {"defect_localization": defect, "rul_prediction": _, "overall_health_score": 100})
    assert status == "CRITICAL"


def test_inside_band_or_no_limit_unchanged(monkeypatch):
    monkeypatch.setattr(ag, "get_vibration_band", lambda mid: (0.0, 0.1))
    assert AgentGamma().process(_frame(0.05))[0].defect_code == "NORMAL"
    monkeypatch.setattr(ag, "get_vibration_band", lambda mid: None)
    assert AgentGamma().process(_frame(0.1176))[0].defect_code == "NORMAL"
