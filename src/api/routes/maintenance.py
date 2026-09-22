"""POST /api/v1/maintenance_complete — reset Alpha buffer and record a Weibull life event."""

from src.api.routes.predict import orchestrator
from src.config import settings
from src.mlops.store import add_life_event
from src.models.repair_events import repair_events
from src.schemas.predictions import MaintenanceCompleteRequest, MaintenanceCompleteResponse
from src.schemas.training import ComponentLifeEvent
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["Closed Loop"])


@router.post("/maintenance_complete", response_model=MaintenanceCompleteResponse)
async def maintenance_complete(req: MaintenanceCompleteRequest):
    baseline_id = orchestrator.reset_machine(req.machine_id)
    repair_events.record(req.machine_id, work_order_id=req.work_order_id, note="Repair signed off")
    recorded = False
    if req.hours_at_event is not None:
        add_life_event(ComponentLifeEvent(
            machineId=req.machine_id,
            component=req.component,
            assetClass=req.asset_class or req.new_bearing_model or settings.DEFAULT_BEARING_TYPE,
            hoursAtInstall=req.hours_at_install,
            hoursAtEvent=req.hours_at_event,
            eventType=req.event_type,
            workOrderId=req.work_order_id,
            notes="Recorded from maintenance_complete",
        ))
        recorded = True
    return MaintenanceCompleteResponse(
        machine_id=req.machine_id,
        buffer_reset=True,
        baseline_id=baseline_id,
        life_event_recorded=recorded,
        message=(
            "Alpha buffer flushed. "
            + ("Life event stored for Weibull fit. " if recorded else "Pass hoursAtEvent + eventType=failed to record a life event. ")
            + "Record the new bearing SKU in the asset master when ERP is connected."
        ),
    )
