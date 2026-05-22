"""Endpoint Server-Sent Events para HU-18 fase 4 + HU-40.

GET /api/realtime/stream — el cliente abre una conexión HTTP de larga duración
y recibe eventos a medida que ocurren en el back. Reemplaza al push FCM.

Eventos emitidos:
  - event: schedule_updated
    data:  { "enabled": bool, "from": "HH:mm", "to": "HH:mm",
             "tz": "...", "updatedAt": <ms>, "updatedBy": "email|null" }
  - event: alerta_nueva  (HU-40)
    data:  { "alerta_id": "...", "tipo": "umbral_errores", "empresa_id": "...",
             "repartidor_id": "...", "repartidor_nombre": "...", "errores_hoy": int,
             "umbral": int, "lat": float|null, "lng": float|null, "ts": <ms> }
    Scope: el back filtra antes de emitir — admin recibe todas, supervisor/
    gerente solo las de su empresa, repartidor no recibe alertas.

HU-40: el endpoint pasó a ser autenticado (CurrentUser). Cookies httpOnly
fluyen automáticamente con EventSource same-origin, y el Android ya manda
Bearer en headers. Sin token → 401.

Heartbeat cada 15s vía sse-starlette `ping` para mantener conexión viva
detrás de proxies que cierran sockets ociosos.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app.core.db import get_session
from app.core.deps import CurrentUser
from app.models.usuario import Dispositivo, Usuario
from app.schemas.posicion import FleetPosition, FleetPositionsResponse
from app.services.realtime import subscribe_alertas, subscribe_schedule

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


def _alerta_in_scope(payload: dict[str, Any], current: dict) -> bool:
    """HU-40 — el supervisor/gerente solo recibe alertas de su empresa.
    Admin recibe todo. Repartidor no recibe alertas."""
    rol = current["rol"]
    if rol == "admin_sistema":
        return True
    if rol in ("supervisor", "gerente"):
        return payload.get("empresa_id") == current.get("empresaId")
    return False


@router.get("/stream")
async def stream(request: Request, current: CurrentUser) -> EventSourceResponse:
    """Multiplex de schedule + alertas en una única conexión SSE.

    Las dos suscripciones a Redis corren en tasks paralelas y empujan a una
    queue compartida; el generator drena la queue y emite SSE. La queue
    tiene cap (drop oldest sería más fancy) — con 100 buffer cubrimos picos.

    Scope: el filtro `_alerta_in_scope` se aplica acá para que la fan-out
    sea per-client. Schedule es global (no requiere filtro).
    """

    async def event_generator() -> AsyncIterator[dict]:
        q: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=100)

        async def relay_schedule() -> None:
            try:
                async for payload in subscribe_schedule():
                    await q.put(("schedule_updated", payload))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("relay_schedule crashed")

        async def relay_alertas() -> None:
            try:
                async for payload in subscribe_alertas():
                    if not _alerta_in_scope(payload, current):
                        continue
                    await q.put(("alerta_nueva", payload))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("relay_alertas crashed")

        sched_task = asyncio.create_task(relay_schedule())
        alert_task = asyncio.create_task(relay_alertas())

        try:
            while True:
                if await request.is_disconnected():
                    logger.debug("SSE client disconnected, cerrando suscripción")
                    break
                try:
                    evt_name, payload = await asyncio.wait_for(q.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    continue  # ping de sse-starlette mantiene la conexión
                yield {"event": evt_name, "data": json.dumps(payload, default=str)}
        finally:
            sched_task.cancel()
            alert_task.cancel()

    return EventSourceResponse(
        event_generator(),
        ping=15,  # heartbeat cada 15s para keep-alive a través de nginx
    )
