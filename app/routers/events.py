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
from sqlalchemy.orm import selectinload

from app.core.audit import log_audit
from app.core.db import get_session
from app.core.deps import CurrentUser
from app.core.observability import events_ingested_total
from app.models.eventos import EventoApp
from app.models.usuario import Dispositivo, Usuario
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
# POST /api/events — HU-03: auth obligatoria (Bearer)
# ─────────────────────────────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=EventCreateResponse)
async def create_event(
    body: EventCreateRequest,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    usuario_id = current["sub"]
    dispositivo_id: str | None = None

    # El deviceUuid del body debe pertenecer al usuario auth.
    # (defensa en profundidad: el JWT identifica al usuario, el deviceUuid al hardware)
    if body.deviceUuid:
        dev = (
            await db.execute(
                select(Dispositivo).where(
                    Dispositivo.deviceUuid == body.deviceUuid,
                    Dispositivo.usuarioId == usuario_id,
                )
            )
        ).scalar_one_or_none()
        if dev is None:
            raise HTTPException(
                status_code=403,
                detail="deviceUuid no pertenece al usuario autenticado",
            )
        dispositivo_id = dev.id
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
    log_audit(
        "event_ingested",
        usuario_id=usuario_id,
        email=current.get("email"),
        empresa_id=current.get("empresaId"),
        tipo=evento.tipo.value,
        dispositivo_id=dispositivo_id,
        screen_name=evento.screenName,
        in_schedule=evento.inSchedule,
    )

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
# GET /api/events
#   ?since=<ms>         eventos con ts > since (polling incremental)
#   ?from=<ms>&to=<ms>  rango temporal (HU-30, para drill-down histórico)
#   ?usuarioId=<id>     filtrar por un repartidor específico (HU-30)
#   ?empresaId=<id>     admin only — filtrar a una empresa (HU-30); supervisor/gerente
#                       siempre van filtrados a SU empresa
# Sin params: últimos 200 dentro del scope que corresponde al rol.
# ─────────────────────────────────────────────────────────────────────────────


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


@router.get("", response_model=EventsListResponse)
async def list_events(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    since: Annotated[int | None, Query()] = None,
    from_: Annotated[int | None, Query(alias="from")] = None,
    to: Annotated[int | None, Query()] = None,
    usuarioId: Annotated[str | None, Query()] = None,
    empresaId: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    # HU-30: scope por rol.
    #   - admin_sistema: ve todo; puede filtrar por empresa con ?empresaId=
    #   - supervisor / gerente: siempre limitado a su empresa (?empresaId=<otra> → 403)
    #   - repartidor: 403 (no consume el feed de admin)
    rol = current["rol"]
    if rol == "repartidor":
        raise HTTPException(status_code=403, detail="Forbidden")

    empresa_scope: str | None = None
    if rol == "admin_sistema":
        empresa_scope = empresaId  # puede ser None → sin filtro
    else:  # supervisor o gerente
        if not current["empresaId"]:
            raise HTTPException(status_code=403, detail="Usuario sin empresa asignada")
        if empresaId and empresaId != current["empresaId"]:
            raise HTTPException(
                status_code=403,
                detail="Solo podés ver eventos de tu propia empresa",
            )
        empresa_scope = current["empresaId"]

    # HU-29: traemos usuario + empresa para que el feed muestre quién hizo qué.
    stmt = (
        select(EventoApp)
        .options(selectinload(EventoApp.usuario).selectinload(Usuario.empresa))
        .order_by(EventoApp.ts.asc())
        .limit(200)
    )

    if empresa_scope is not None:
        # subquery evita problemas con eager loading + join explícito
        stmt = stmt.where(
            EventoApp.usuarioId.in_(
                select(Usuario.id).where(Usuario.empresaId == empresa_scope)
            )
        )

    if usuarioId is not None:
        stmt = stmt.where(EventoApp.usuarioId == usuarioId)

    if since is not None:
        try:
            since_dt = _ms_to_dt(since)
        except (OverflowError, OSError, ValueError) as e:
            raise HTTPException(status_code=422, detail="since must be a number") from e
        stmt = stmt.where(EventoApp.ts > since_dt)

    if from_ is not None:
        try:
            stmt = stmt.where(EventoApp.ts >= _ms_to_dt(from_))
        except (OverflowError, OSError, ValueError) as e:
            raise HTTPException(status_code=422, detail="from must be a number") from e

    if to is not None:
        try:
            stmt = stmt.where(EventoApp.ts <= _ms_to_dt(to))
        except (OverflowError, OSError, ValueError) as e:
            raise HTTPException(status_code=422, detail="to must be a number") from e

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
            usuarioId=e.usuario.id if e.usuario else None,
            usuarioEmail=e.usuario.email if e.usuario else None,
            usuarioNombre=e.usuario.nombre if e.usuario else None,
            empresaId=e.usuario.empresa.id if (e.usuario and e.usuario.empresa) else None,
            empresaNombre=e.usuario.empresa.nombre if (e.usuario and e.usuario.empresa) else None,
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
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> BulkEventsResponse:
    """Ingesta de hasta 500 eventos en una sola transacción.

    HU-03: requiere Bearer. Todos los eventos quedan asociados al usuario auth.
    Los deviceUuids del body que no pertenezcan al usuario se descartan silenciosamente
    (no rompe el batch — la app puede llevar dos dispositivos en distintos momentos).
    """
    usuario_id = current["sub"]

    # 1) Recolectar deviceUuids únicos y resolverlos al usuario auth
    uuids = {e.deviceUuid for e in body.events if e.deviceUuid}
    dev_map: dict[str, str] = {}
    if uuids:
        devs = (
            await db.execute(
                select(Dispositivo.id, Dispositivo.deviceUuid).where(
                    Dispositivo.deviceUuid.in_(uuids),
                    Dispositivo.usuarioId == usuario_id,
                )
            )
        ).all()
        dev_map = {d.deviceUuid: d.id for d in devs}

    # 2) Armar las filas para bulk insert
    rows: list[dict[str, Any]] = []
    touched_devices: set[str] = set()
    for ev in body.events:
        dispositivo_id: str | None = None
        if ev.deviceUuid and ev.deviceUuid in dev_map:
            dispositivo_id = dev_map[ev.deviceUuid]
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

    # 4) Métricas + audit
    tipos_count: dict[str, int] = {}
    for ev in body.events:
        events_ingested_total.labels(tipo=ev.type.value).inc()
        tipos_count[ev.type.value] = tipos_count.get(ev.type.value, 0) + 1
    log_audit(
        "events_bulk_ingested",
        usuario_id=usuario_id,
        email=current.get("email"),
        empresa_id=current.get("empresaId"),
        accepted=len(rows),
        tipos=tipos_count,
        devices_touched=len(touched_devices),
    )

    return BulkEventsResponse(accepted=len(rows), queuedJobId="inline")
