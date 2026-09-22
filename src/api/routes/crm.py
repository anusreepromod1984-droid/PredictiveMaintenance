"""GET /api/v1/crm — Greenbotz demo SAP PM board."""

from fastapi import APIRouter

from src.models.demo_crm import build_demo_crm

router = APIRouter(prefix="/api/v1", tags=["Demo CRM"])


@router.get("/crm")
async def get_demo_crm():
    return build_demo_crm()
