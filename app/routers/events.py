"""Endpoints de events — port de cognipilot-remote/app/api/events/route.ts + bulk.

POST /api/events       : ingesta unitaria (compat con app actual)
POST /api/events/bulk  : ingesta en batch — bulk insert en una transacción (más eficiente
                          que loops de INSERT individuales bajo carga)
GET  /api/events       : feed para el admin (últimos 200 o desde ?since=)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.observability import events_ingested_total
from app.models.eventos import EventoApp
from app.models.usuario import Dispositivo
from app.schemas.evento import (
    BulkEventsRequest,
    BulkEventsResponse,
    EventCompactResponse,
    EventCreateRequest,
    EventCreateResponse,
    EventsListResponse,
)

router = APIRouter(prefix="/api/events", tags=["events"])


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/events
# ─────────────────────────────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=EventCreateResponse)
async def create_event(
    body: EventCreateRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    dispositivo_id: str | None = None
    usuario_id: str | None = None

    if body.deviceUuid:
        dev = (
            await db.execute(
                select(Dispositivo).where(Dispositivo.deviceUuid == body.deviceUuid)
            )
        ).scalar_one_or_none()
        if dev is not None:
            dispositivo_id = dev.id
            usuario_id = dev.usuarioId
            # Touch lastSeen (sin tocar lastLat/Lng — eso va por /api/positions)
            dev.lastSeen = datetime.now(timezone.utc)

    evento = EventoApp(
        tipo=body.type,
        usuarioId=usuario_id,
        dispositivoId=dispositivo_id,
        inSchedule=body.inSchedule,
        screenName=body.screenName,
        appPackage=body.appPackage,
        keywords=body.keywords,
        screenText=body.screenText,
    )
    db.add(evento)
    await db.commit()
    await db.refresh(evento)

    events_ingested_total.labels(tipo=evento.tipo.value).inc()

    return {
        "ok": True,
        "event": {
            "id": evento.id,
            "tipo": evento.tipo.value,
            "ts": evento.ts.isoformat(),
            "usuarioId": evento.usuarioId,
            "dispositivoId": evento.dispositivoId,
            "inSchedule": evento.inSchedule,
            "screenName": evento.screenName,
            "appPackage": evento.appPackage,
            "keywords": evento.keywords,
            "screenText": evento.screenText,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/events?since=<ms>  → eventos con ts > since
# GET /api/events             → últimos 200
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=EventsListResponse)
async def list_events(
    db: Annotated[AsyncSession, Depends(get_session)],
    since: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(EventoApp).order_by(EventoApp.ts.asc()).limit(200)
    if since is not None:
        try:
            since_dt = datetime.fromtimestamp(since / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as e:
            raise HTTPException(status_code=422, detail="since must be a number") from e
        stmt = stmt.where(EventoApp.ts > since_dt)

    eventos = (await db.execute(stmt)).scalars().all()
    events = [
        EventCompactResponse(
            id=e.id,
            type=e.tipo,
            timestamp=int(e.ts.timestamp() * 1000),
            deviceId=e.dispositivoId or "unknown",
            inSchedule=e.inSchedule,
            screenName=e.screenName,
            appPackage=e.appPackage,
            keywords=e.keywords,
            screenText=e.screenText,
        ).model_dump()
        for e in eventos
    ]
    return {"events": events, "serverTime": int(datetime.now(timezone.utc).timestamp() * 1000)}


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/events/bulk — batch insert para alto throughput
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/bulk", status_code=202, response_model=BulkEventsResponse)
async def bulk_events(
    body: BulkEventsRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> BulkEventsResponse:
    """Ingesta de hasta 500 eventos en una sola transacción.

    Optimizaciones:
      - Una sola consulta para resolver todos los deviceUuids únicos
      - Bulk INSERT de eventos
      - Touch lastSeen de los dispositivos involucrados en una sola UPDATE

    El device se queda con la última hora de inserción en `lastSeen`.
    """
    # 1) Recolectar deviceUuids únicos y resolverlos a dispositivo_id + usuario_id
    uuids = {e.deviceUuid for e in body.events if e.deviceUuid}
    dev_map: dict[str, tuple[str, str]] = {}
    if uuids:
        devs = (
            await db.execute(
                select(Dispositivo.id, Dispositivo.usuarioId, Dispositivo.deviceUuid).where(
                    Dispositivo.deviceUuid.in_(uuids)
                )
            )
        ).all()
        dev_map = {d.deviceUuid: (d.id, d.usuarioId) for d in devs}

    # 2) Armar las filas para bulk insert
    rows: list[dict[str, Any]] = []
    touched_devices: set[str] = set()
    for ev in body.events:
        dispositivo_id: str | None = None
        usuario_id: str | None = None
        if ev.deviceUuid and ev.deviceUuid in dev_map:
            dispositivo_id, usuario_id = dev_map[ev.deviceUuid]
            touched_devices.add(dispositivo_id)
        rows.append({
            "tipo": ev.type,
            "usuarioId": usuario_id,
            "dispositivoId": dispositivo_id,
            "inSchedule": ev.inSchedule,
            "screenName": ev.screenName,
            "appPackage": ev.appPackage,
            "keywords": ev.keywords,
            "screenText": ev.screenText,
        })

    # 3) Bulk INSERT + touch lastSeen en una sola transacción
    if rows:
        await db.execute(insert(EventoApp), rows)
    if touched_devices:
        await db.execute(
            update(Dispositivo)
            .where(Dispositivo.id.in_(touched_devices))
            .values(lastSeen=datetime.now(timezone.utc))
        )
    await db.commit()

    # 4) Métricas
    for ev in body.events:
        events_ingested_total.labels(tipo=ev.type.value).inc()

    return BulkEventsResponse(accepted=len(rows), queuedJobId="inline")
