"""CogniPilot Back — FastAPI app entrypoint.

Levanta la app, configura logging, CORS, monta los routers.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.routers import auth, devices, empresas, events, health, schedule, usuarios

# Configurar logging
settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hooks."""
    logger.info(
        "CogniPilot Back starting up — env=%s, db=%s",
        settings.app_env,
        settings.database_url.split("@")[-1],  # solo host:puerto/db, sin creds
    )
    yield
    logger.info("CogniPilot Back shutting down")


app = FastAPI(
    title="CogniPilot Back",
    description="API de CogniPilot — FastAPI + Postgres + Redis + arq workers",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.is_dev else None,
    redoc_url="/redoc" if settings.is_dev else None,
)

# CORS (necesario si front corre en otro origen durante dev)
if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Mount routers
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(empresas.router)
app.include_router(usuarios.router)
app.include_router(schedule.router)
app.include_router(events.router)
app.include_router(devices.router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "env": settings.app_env,
        "docs": "/docs",
    }
