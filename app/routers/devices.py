"""Endpoints de dispositivos.

POST /api/devices/register  — upsert desde la app Android (cualquier user auth).
GET  /api/devices            — HU-35: listado plano con scope por rol.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_session
from app.core.deps import CurrentUser
from app.models.usuario import Dispositivo, Usuario
from app.schemas.dispositivo import (
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceShortResponse,
    DispositivoRow,
    DispositivosListResponse,
)

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.post("/register", response_model=DeviceRegisterResponse)
async def register_device(
    body: DeviceRegisterRequest,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceRegisterResponse:
    existing = (
        await db.execute(
            select(Dispositivo).where(Dispositivo.deviceUuid == body.deviceUuid)
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.usuarioId = current["sub"]
        existing.fcmToken = body.fcmToken
        existing.modelo = body.modelo
        existing.osVersion = body.osVersion
        existing.appVersion = body.appVersion
        existing.lastSeen = datetime.now(timezone.utc)
        existing.activo = True
        dispositivo = existing
    else:
        dispositivo = Dispositivo(
            usuarioId=current["sub"],
            deviceUuid=body.deviceUuid,
            fcmToken=body.fcmToken,
            modelo=body.modelo,
            osVersion=body.osVersion,
            appVersion=body.appVersion,
        )
        db.add(dispositivo)
        await db.flush()

    await db.commit()
    return DeviceRegisterResponse(
        dispositivo=DeviceShortResponse(id=dispositivo.id, deviceUuid=dispositivo.deviceUuid)
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/devices — HU-35: listado plano con scope por rol
#   admin global; supervisor su empresa; gerente/repartidor 403.
#   Filtros opcionales: ?empresaId, ?usuarioId, ?activo, ?conexion
# ─────────────────────────────────────────────────────────────────────────────


def _aware_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _connection_state(last_seen: datetime, now: datetime) -> str:
    delta = now - _aware_utc(last_seen)
    if delta < timedelta(minutes=5):
        return "online"
    if delta < timedelta(hours=24):
        return "active_today"
    return "offline"


@router.get("", response_model=DispositivosListResponse)
async def list_dispositivos(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    empresaId: Annotated[str | None, Query()] = None,
    usuarioId: Annotated[str | None, Query()] = None,
    activo: Annotated[bool | None, Query()] = None,
    conexion: Annotated[Literal["online", "active_today", "offline"] | None, Query()] = None,
) -> DispositivosListResponse:
    rol = current["rol"]
    if rol in ("gerente", "repartidor"):
        raise HTTPException(status_code=403, detail="Forbidden")

    empresa_scope: str | None = None
    if rol == "admin_sistema":
        empresa_scope = empresaId
    else:  # supervisor
        if not current["empresaId"]:
            raise HTTPException(status_code=403, detail="Usuario sin empresa asignada")
        if empresaId and empresaId != current["empresaId"]:
            raise HTTPException(status_code=403, detail="Solo podés ver tu propia empresa")
        empresa_scope = current["empresaId"]

    stmt = (
        select(Dispositivo)
        .options(selectinload(Dispositivo.usuario).selectinload(Usuario.empresa))
        .order_by(Dispositivo.lastSeen.desc())
    )
    if empresa_scope is not None:
        stmt = stmt.where(
            Dispositivo.usuarioId.in_(
                select(Usuario.id).where(Usuario.empresaId == empresa_scope)
            )
        )
    if usuarioId is not None:
        stmt = stmt.where(Dispositivo.usuarioId == usuarioId)
    if activo is not None:
        stmt = stmt.where(Dispositivo.activo == activo)

    devs = (await db.execute(stmt)).scalars().all()
    now = datetime.now(timezone.utc)

    rows: list[DispositivoRow] = []
    for d in devs:
        state = _connection_state(d.lastSeen, now)
        if conexion is not None and state != conexion:
            continue
        u = d.usuario
        rows.append(DispositivoRow(
            id=d.id,
            deviceUuid=d.deviceUuid,
            modelo=d.modelo,
            osVersion=d.osVersion,
            appVersion=d.appVersion,
            activo=d.activo,
            lastSeen=int(_aware_utc(d.lastSeen).timestamp() * 1000),
            lastLat=float(d.lastLat) if d.lastLat is not None else None,
            lastLng=float(d.lastLng) if d.lastLng is not None else None,
            createdAt=int(_aware_utc(d.createdAt).timestamp() * 1000),
            connectionState=state,
            usuarioId=u.id,
            usuarioNombre=u.nombre,
            usuarioEmail=u.email,
            usuarioRol=u.rol.value,
            empresaId=u.empresaId,
            empresaNombre=u.empresa.nombre if u.empresa else None,
        ))

    return DispositivosListResponse(dispositivos=rows)
