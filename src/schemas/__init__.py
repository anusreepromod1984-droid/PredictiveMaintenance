"""
Schemas Package Initializer
Export Modular Pydantic v2 Product Data Contracts.
"""

from src.schemas.telemetry import TelemetryFrame, HarmonicPeak
from src.schemas.products import ProductATelemetry, ProductBTelemetry, ProductCTelemetry
from src.schemas.predictions import (
    PredictRULResponse,
    HealthStatusResponse,
    ThermalDeWeathering,
    CableCheckStatus,
    DefectLocalization,
    RULPrediction,
    ElectricalHealth,
    CMMSWorkOrder
)

__all__ = [
    "TelemetryFrame",
    "HarmonicPeak",
    "ProductATelemetry",
    "ProductBTelemetry",
    "ProductCTelemetry",
    "PredictRULResponse",
    "HealthStatusResponse",
    "ThermalDeWeathering",
    "CableCheckStatus",
    "DefectLocalization",
    "RULPrediction",
    "ElectricalHealth",
    "CMMSWorkOrder"
]
