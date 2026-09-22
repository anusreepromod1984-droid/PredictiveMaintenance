"""
FastAPI Core Prediction Endpoint
POST /api/v1/predict_rul
Executes 4-Agent LangGraph pipeline (Alpha->Beta->Gamma->Delta) for real-time diagnosis & RUL countdown.
"""

from fastapi import APIRouter, HTTPException, status
from typing import List, Dict, Any
from src.schemas.telemetry import TelemetryFrame
from src.schemas.predictions import PredictRULResponse
from src.agents.orchestrator import APMSOrchestrator
from src.services.event_historian import event_historian
from src.agents.open_wo_tracker import wo_tracker
from src.models.pdm_program import build_rul_history
from src.utils.logger import get_logger

logger = get_logger("API.Predict")
router = APIRouter(prefix="/api/v1", tags=["Predictive AI Engine"])

# Initialize single orchestrator instance
orchestrator = APMSOrchestrator()


@router.post(
    "/predict_rul",
    response_model=PredictRULResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute 4-Agent LangGraph RUL & Fault Diagnostics Pipeline"
)
async def predict_rul(frame: TelemetryFrame):
    """
    Ingests a telemetry frame and runs Alpha → Beta → Gamma → (skip|Delta):
    1. **Agent Alpha:** NAMUR quality + 5-pattern contract
    2. **Agent Beta:** De-weathering + isolated_failure_domain
    3. **Agent Gamma:** Catalog diagnosis + physics/Weibull component RUL
    4. **Agent Delta:** PdM or condition-PM advisory (CMMS draft until ERP is connected)
    """
    try:
        response = orchestrator.run(frame)
        return response
    except Exception as e:
        logger.error(f"Prediction failed for machine {frame.machine_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal AI Pipeline Error: {str(e)}"
        )


@router.get(
    "/predict/history/{machine_id}",
    summary="Get Recent Diagnostic History for Asset",
    response_model=List[Dict[str, Any]]
)
async def get_diagnosis_history(machine_id: str, limit: int = 20):
    """Returns recent diagnosis runs and trace records for the specified asset."""
    return event_historian.get_recent_diagnoses(machine_id, limit=limit)


@router.get(
    "/predict/events/{machine_id}",
    summary="Get Recent Sensor Cable / Halt Events for Asset",
    response_model=List[Dict[str, Any]]
)
async def get_sensor_events(machine_id: str, limit: int = 20):
    """Returns recent sensor quality faults, flatline locks, and cable disconnects."""
    return event_historian.get_recent_events(machine_id, limit=limit)


@router.get(
    "/predict/open_work_orders",
    summary="List Currently Active Open Work Orders",
    response_model=List[Dict[str, Any]]
)
async def get_open_work_orders(machine_id: str = None):
    """Returns all active open work orders tracked by Agent Delta."""
    return wo_tracker.list_open_work_orders(machine_id=machine_id)


@router.get(
    "/predict/rul_history/{machine_id}",
    summary="RUL series with diagnosis call and repair markers",
)
async def get_rul_history(machine_id: str):
    return build_rul_history(machine_id)
