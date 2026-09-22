"""
FastAPI Thermal Normalization Endpoint
POST /api/v1/de_weather
Executes Agent Beta dynamic de-weathering to isolate friction heat from ambient summer heat drift.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from src.schemas.predictions import ThermalDeWeathering
from src.agents.agent_beta import AgentBeta

router = APIRouter(prefix="/api/v1", tags=["Thermal Normalization"])
agent_beta = AgentBeta()


class DeWeatherRequest(BaseModel):
    temp_motor: float = Field(..., alias="tempMotor", description="Electric motor winding temperature (°C)")
    temp_ambient: float = Field(..., alias="tempAmbient", description="Ambient factory temperature (°C)")


@router.post(
    "/de_weather",
    response_model=ThermalDeWeathering,
    summary="Agent Beta Ambient Heat Drift Compensation"
)
async def de_weather(req: DeWeatherRequest):
    """
    Normalizes summer heat drift (+15°C) to suppress false thermal shutdown alarms.
    """
    delta_temp = req.temp_motor - req.temp_ambient
    ambient_drift = req.temp_ambient - 25.0
    compensated_delta = delta_temp - max(0.0, ambient_drift)

    return ThermalDeWeathering(
        delta_temperature_c=round(delta_temp, 2),
        ambient_drift_compensated=ambient_drift > 5.0,
        genuine_thermal_overheating=compensated_delta > 35.0
    )
