"""
Production FastAPI Main Application Module
Siemens / Bosch Industrial Grade Web API Server Entrypoint
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.utils.logger import get_logger
from src.api.auth import api_key_middleware
from src.api.routes import health_router, predict_router, de_weather_router, maintenance_router, training_router, assets_router, set_mqtt_service, config_router, crm_router, program_router, oem_mail_router
from src.services.mqtt_service import MQTTIngestionService
from src.api.socketio_server import sio, set_mqtt_service_for_sio
import socketio as _sio_lib

logger = get_logger("API.Main")

# Initialize MQTT background service
mqtt_service = MQTTIngestionService()
set_mqtt_service(mqtt_service)

docs_url = "/docs" if settings.DOCS_ENABLED else None
redoc_url = "/redoc" if settings.DOCS_ENABLED else None

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Agentic AI Predictive & Preventive Maintenance System (APMS) - Production REST API",
    docs_url=docs_url,
    redoc_url=redoc_url,
)

cors_origins = settings.cors_origin_list()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_origins != ["*"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

app.middleware("http")(api_key_middleware)


@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/dashboard"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

app.include_router(health_router)
app.include_router(predict_router)
app.include_router(de_weather_router)
app.include_router(maintenance_router)
app.include_router(training_router)
app.include_router(assets_router)
app.include_router(config_router)
app.include_router(crm_router)
app.include_router(program_router)
app.include_router(oem_mail_router)

if os.path.exists("public"):
    app.mount("/dashboard", StaticFiles(directory="public", html=True), name="dashboard")


@app.on_event("startup")
async def startup_event():
    logger.info(f"=== Starting {settings.APP_NAME} v{settings.APP_VERSION} ===")
    logger.info(f"Environment: {settings.ENVIRONMENT} | Host: {settings.HOST}:{settings.PORT}")
    logger.info("LangGraph 4-Agent Pipeline Loaded Successfully (Alpha -> Beta -> Gamma -> Delta).")
    logger.info(f"CORS origins: {cors_origins} | API auth: {settings.auth_required()}")
    set_mqtt_service_for_sio(mqtt_service)
    logger.info("[SocketIO] Real-time Socket.IO server active at /socket.io")
    if settings.TELEMETRY_PERSIST_ENABLED:
        from src.database.telemetry_store import ensure_telemetry_schema
        if ensure_telemetry_schema():
            logger.info("[Postgres] MQTT frames → telemetry_reading; agent runs → agent_snapshot")
        else:
            logger.warning("[Postgres] Unreachable — MQTT stays in RAM until the database is up")
    if settings.MQTT_ENABLED:
        logger.info(f"Starting MQTT Broker Subscriber ({settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT})...")
        mqtt_service.start()


@app.on_event("shutdown")
async def shutdown_event():
    logger.info(f"=== Shutting down {settings.APP_NAME} ===")
    mqtt_service.stop()


# uvicorn src.api.main:application — Socket.IO owns /socket.io; everything else is FastAPI.
application = _sio_lib.ASGIApp(sio, other_asgi_app=app)
