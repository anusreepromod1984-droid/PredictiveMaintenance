"""Training-data contracts the plant must fill. These are labels, not telemetry."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

LifeEventType = Literal["failed", "replaced_pm", "still_running"]
FaultLabel = Literal["NORMAL", "BPFI", "BPFO", "MF001", "MF002", "MF003", "EF001", "SENSOR"]
LabelSource = Literal["teardown", "wo", "technician", "commissioning"]


class ComponentLifeEvent(BaseModel):
    """One component life. Required to fit Weibull."""
    machine_id: str = Field(..., alias="machineId")
    component: str = Field(..., description="DE_bearing, NDE_bearing, winding, coupling")
    asset_class: str = Field(default="SKF-6208", alias="assetClass")
    hours_at_install: float = Field(default=0.0, alias="hoursAtInstall")
    hours_at_event: float = Field(..., alias="hoursAtEvent", description="Run-hours at failure, PM replace, or now if still_running")
    event_type: LifeEventType = Field(..., alias="eventType")
    event_date: Optional[str] = Field(default=None, alias="eventDate")
    work_order_id: Optional[str] = Field(default=None, alias="workOrderId")
    notes: Optional[str] = None
    recorded_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"populate_by_name": True}

    @property
    def duration_hours(self) -> float:
        return max(0.0, self.hours_at_event - self.hours_at_install)

    @property
    def observed_failure(self) -> bool:
        return self.event_type == "failed"


class LabeledWindow(BaseModel):
    """One confirmed diagnosis window. Required to train the classifier."""
    machine_id: str = Field(..., alias="machineId")
    timestamp: str
    label: FaultLabel
    source: LabelSource = "technician"
    features: Dict[str, Any] = Field(default_factory=dict)
    notes: Optional[str] = None
    recorded_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"populate_by_name": True}


class TrajectoryPoint(BaseModel):
    """One sample on a run-to-failure curve. Required to train a PINN."""
    run_hours: float = Field(..., alias="runHours")
    imu_acceleration: float = Field(..., alias="imuAcceleration")
    temp_compressor: float = Field(..., alias="tempCompressor")
    em_machine_load: float = Field(default=70.0, alias="emMachineLoad")
    rpm: float = 1480.0
    true_rul_hours: float = Field(..., alias="trueRulHours")

    model_config = {"populate_by_name": True}


class RulTrajectory(BaseModel):
    trajectory_id: str = Field(..., alias="trajectoryId")
    machine_id: str = Field(..., alias="machineId")
    component: str = "DE_bearing"
    asset_class: str = Field(default="SKF-6208", alias="assetClass")
    points: List[TrajectoryPoint]

    model_config = {"populate_by_name": True}


class FeedbackRequest(BaseModel):
    machine_id: str = Field(..., alias="machineId")
    label: FaultLabel
    source: LabelSource = "technician"
    timestamp: Optional[str] = None
    features: Dict[str, Any] = Field(default_factory=dict)
    notes: Optional[str] = None

    model_config = {"populate_by_name": True}


class FitRequest(BaseModel):
    asset_class: str = Field(default="SKF-6208", alias="assetClass")
    experimental: bool = Field(default=False, description="Allow fit below production minima")

    model_config = {"populate_by_name": True}


class GbotzBackfillRequest(BaseModel):
    """Pull persisted Gbotz history into telemetry_reading. Does not train XGBoost."""
    duration: str = Field(default="14d", description="Relative window ending at now, e.g. 7d or 14d")
    max_points: int = Field(default=300, alias="maxPoints")
    machine_ids: Optional[List[str]] = Field(default=None, alias="machineIds")
    fit_after: bool = Field(default=True, alias="fitAfter")
    experimental: bool = True

    model_config = {"populate_by_name": True}


class DatasetStatus(BaseModel):
    weibull: Dict[str, Any]
    classifier: Dict[str, Any]
    pinn: Dict[str, Any]
    you_must_provide: List[str]
    registry: Dict[str, Any]
