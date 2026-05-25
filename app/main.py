"""CogniPilot Back — FastAPI app entrypoint.

Levanta la app, configura logging, CORS, instrumentación Prometheus, monta routers.
Lifespan: inicializa el pool de arq (Redis) y un loop async que refresca los gauges
de active_devices y queue_depth cada 30s.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app.core.arq_client import close_arq_pool, get_arq_pool, init_arq_pool
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.observability import active_devices, make_instrumentator, queue_depth
from app.models.usuario import Dispositivo
from app.routers import (
    admin_settings,
    alertas,
    auditoria,
    auth,
    devices,
    empresas,
    events,
    health,
    me,
    metrics,
    positions,
    realtime,
    reglas,
    reportes,
    reset_password,
    schedule,
    system,
    usuarios,
)

# Configurar logging
settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Background loop — refresca gauges de "estado vivo" cada 30s
# ─────────────────────────────────────────────────────────────────────────────


async def _refresh_live_gauges() -> None:
    """Actualiza cognipilot_active_devices y cognipilot_arq_queue_depth.

    Corre dentro del proceso de la API para que `/metrics` lo exponga al scrape
    de Prometheus. Es un loop perpetuo cancelable desde el lifespan.
    """
    while True:
        try:
            now = datetime.now(timezone.utc)
            async with SessionLocal() as db:
                c5m = (
                    await db.execute(
                        select(func.count(Dispositivo.id)).where(
                            Dispositivo.lastSeen >= now - timedelta(minutes=5)
                        )
                    )
                ).scalar() or 0
                c24h = (
                    await db.execute(
                        select(func.count(Dispositivo.id)).where(
                            Dispositivo.lastSeen >= now - timedelta(hours=24)
                        )
                    )
                ).scalar() or 0
            active_devices.labels(window="5m").set(c5m)
            active_devices.labels(window="24h").set(c24h)

            # arq guarda los jobs pendientes como sorted set "arq:queue".
            # zcard nos da la profundidad (sin scorear).
            try:
                arq = get_arq_pool()
                depth = await arq.zcard("arq:queue")  # type: ignore[attr-defined]
                queue_depth.labels(queue="arq:queue").set(int(depth))
            except Exception:  # noqa: BLE001
                # Si Redis está caído o arq no inicializado, no es crítico.
                pass

        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("refresh_live_gauges iteration failed")

        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "CogniPilot Back starting up — env=%s, db=%s",
        settings.app_env,
        settings.database_url.split("@")[-1],
    )

    # Inicializar pool arq. Si Redis no está disponible, los endpoints que
    # dependen de enqueueing harán fallback a sync (FCM) o devolverán 503.
    try:
        await init_arq_pool()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not initialize arq pool at startup: %s", e)

    # Loop de actualización de gauges
    gauges_task = asyncio.create_task(_refresh_live_gauges())

    try:
        yield
    finally:
        gauges_task.cancel()
        try:
            await gauges_task
        except asyncio.CancelledError:
            pass
        await close_arq_pool()
        logger.info("CogniPilot Back shutting down")


# ─────────────────────────────────────────────────────────────────────────────
# App factory
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CogniPilot Back",
    description="API de CogniPilot — FastAPI + Postgres + Redis + arq workers",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.is_dev else None,
    redoc_url="/redoc" if settings.is_dev else None,
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# HU-47 — Middleware HTTP recientes (ring buffer Redis)
# Skip /api/system/* y /metrics: el primero loopea con el auto-refresh del
# panel de peticiones; el segundo lo scrappea Prometheus cada 15s y satura
# el ring. Mantener el set chico — el endpoint /api/system/requests también
# está excluido por el prefix /api/system.
# ─────────────────────────────────────────────────────────────────────────────


_HTTP_RECENT_SKIP_PREFIXES = ("/api/system", "/metrics")


@app.middleware("http")
async def http_recent_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith(_HTTP_RECENT_SKIP_PREFIXES):
        return await call_next(request)

    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # X-Forwarded-For respetado (nginx delante). Tomamos solo el primer hop.
        xff = request.headers.get("x-forwarded-for") or ""
        client_ip = xff.split(",")[0].strip() if xff else (request.client.host if request.client else None)
        from app.services import http_recent as _hr
        try:
            await _hr.push({
                "ts": int(datetime.now(timezone.utc).timestamp() * 1000),
                "method": request.method,
                "path": path,
                "query": request.url.query or "",
                "status": status,
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                "client_ip": client_ip,
                "user_agent": (request.headers.get("user-agent") or "")[:120],
            })
        except Exception:  # noqa: BLE001
            # http_recent.push ya hace su propio try/except — esto es defensa extra.
            pass

# Prometheus instrumentation — expone /metrics y trackea HTTP automáticamente
instrumentator = make_instrumentator()
instrumentator.instrument(app)
instrumentator.expose(app, endpoint="/metrics", include_in_schema=False)

# Mount routers
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(empresas.router)
app.include_router(usuarios.router)
app.include_router(schedule.router)
app.include_router(events.router)
app.include_router(devices.router)
app.include_router(positions.router)
app.include_router(realtime.router)
app.include_router(metrics.router)
app.include_router(reportes.router)
app.include_router(me.router)
app.include_router(auditoria.router)
app.include_router(reglas.router)
app.include_router(alertas.router)
app.include_router(admin_settings.router)
app.include_router(reset_password.public_router)
app.include_router(reset_password.admin_router)
app.include_router(system.router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "env": settings.app_env,
        "docs": "/docs",
    }
