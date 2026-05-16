"""Endpoints de events — port de cognipilot-remote/app/api/events/route.ts.

POST /api/events: ingesta unitaria (compat con app actual).
GET  /api/events: feed para el admin (últimos 200 por defecto, o desde ?since=).

⚠️ FASE B: agregar POST /api/events/bulk con cola arq para alto throughput.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.eventos import EventoApp
from app.models.usuario import Dispositivo
from app.schemas.evento import (
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
