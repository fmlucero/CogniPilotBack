"""Endpoint Server-Sent Events para HU-18 fase 4.

GET /api/realtime/stream — el cliente abre una conexión HTTP de larga duración
y recibe eventos a medida que ocurren en el back. Reemplaza al push FCM.

Eventos emitidos:
  - event: schedule_updated
    data:  { "enabled": bool, "from": "HH:mm", "to": "HH:mm",
             "tz": "...", "updatedAt": <ms>, "updatedBy": "email|null" }

El cliente Android (RealtimeStreamClient con OkHttp EventSource) consume esto
en foreground. Si la conexión cae (red mala, app en bg), reconecta o cae al
polling del WorkManager.

Heartbeat cada 15s vía sse-starlette `ping` para mantener conexión viva
detrás de proxies que cierran sockets ociosos (nginx tiene proxy_read_timeout
30s en nuestra config — el ping mantiene la conexión).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app.core.db import get_session
from app.core.deps import CurrentUser
from app.models.usuario import Dispositivo, Usuario
from app.schemas.posicion import FleetPosition, FleetPositionsResponse
from app.services.realtime import subscribe_schedule

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/realtime", tags=["realtime"])


def _aware_utc(dt: datetime) -> datetime:
    """Normaliza a tz-aware UTC (mismo helper que usuarios.py — datos legacy
    de Prisma vienen tz-naive aunque la columna sea tz-aware)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _connection_state(last_seen: datetime, now: datetime) -> str:
    delta = now - _aware_utc(last_seen)
    if delta < timedelta(minutes=5):
        return "online"
    if delta < timedelta(hours=24):
        return "active_today"
    return "offline"


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/realtime/positions  — HU-11 mapa flota
# Snapshot de última posición por repartidor con scope por rol:
#   - admin_sistema: todos los dispositivos con lastLat/lastLng (ult. 24h por defecto)
#   - supervisor/gerente: solo los de su empresa
#   - repartidor: 403
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/positions", response_model=FleetPositionsResponse)
async def fleet_positions(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FleetPositionsResponse:
    rol = current["rol"]
    if rol == "repartidor":
        raise HTTPException(status_code=403, detail="Forbidden")

    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    stmt = (
        select(Dispositivo)
        .options(selectinload(Dispositivo.usuario).selectinload(Usuario.empresa))
        .where(
            Dispositivo.lastLat.is_not(None),
            Dispositivo.lastLng.is_not(None),
            Dispositivo.lastSeen >= cutoff_24h,
        )
        .order_by(Dispositivo.lastSeen.desc())
    )

    # Scope por empresa (subquery para coexistir con eager loading)
    if rol in ("supervisor", "gerente"):
        if not current["empresaId"]:
            raise HTTPException(status_code=403, detail="Usuario sin empresa asignada")
        stmt = stmt.where(
            Dispositivo.usuarioId.in_(
                select(Usuario.id).where(Usuario.empresaId == current["empresaId"])
            )
        )

    devs = (await db.execute(stmt)).scalars().all()

    # Un repartidor puede tener varios dispositivos — nos quedamos con el más
    # reciente por usuario (ya viene ordenado desc por lastSeen).
    seen_users: set[str] = set()
    positions: list[FleetPosition] = []
    for d in devs:
        if d.usuarioId in seen_users:
            continue
        seen_users.add(d.usuarioId)
        u = d.usuario
        positions.append(
            FleetPosition(
                usuarioId=u.id,
                usuarioNombre=u.nombre,
                usuarioEmail=u.email,
                empresaId=u.empresaId,
                empresaNombre=u.empresa.nombre if u.empresa else None,
                dispositivoId=d.id,
                deviceUuid=d.deviceUuid,
                lat=float(d.lastLat),  # type: ignore[arg-type]
                lng=float(d.lastLng),  # type: ignore[arg-type]
                lastSeen=int(_aware_utc(d.lastSeen).timestamp() * 1000),
                connectionState=_connection_state(d.lastSeen, now),
            )
        )

    return FleetPositionsResponse(
        positions=positions,
        serverTime=int(now.timestamp() * 1000),
    )


@router.get("/stream")
async def stream(request: Request) -> EventSourceResponse:
    """Suscribe el cliente al channel schedule de Redis y va emitiendo
    eventos SSE. Maneja desconexión limpia cuando el cliente cierra el socket.
    """

    async def event_generator() -> AsyncIterator[dict]:
        async for payload in subscribe_schedule():
            if await request.is_disconnected():
                logger.debug("SSE client disconnected, cerrando suscripción")
                break
            yield {
                "event": "schedule_updated",
                "data": json.dumps(payload),
            }

    return EventSourceResponse(
        event_generator(),
        ping=15,  # heartbeat cada 15s para keep-alive a través de nginx
    )
