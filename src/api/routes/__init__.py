"""
API Routes Package Initializer
"""
from src.api.routes.health import router as health_router
from src.api.routes.predict import router as predict_router
from src.api.routes.de_weather import router as de_weather_router
from src.api.routes.maintenance import router as maintenance_router
from src.api.routes.training import router as training_router
from src.api.routes.assets import router as assets_router, set_mqtt_service
from src.api.routes.config import router as config_router
from src.api.routes.crm import router as crm_router
from src.api.routes.program import router as program_router
from src.api.routes.oem_mail import router as oem_mail_router
from src.api.routes.alerts import router as alerts_router

__all__ = [
    "health_router",
    "predict_router",
    "de_weather_router",
    "maintenance_router",
    "training_router",
    "assets_router",
    "set_mqtt_service",
    "config_router",
    "crm_router",
    "program_router",
    "oem_mail_router",
    "alerts_router",
]
