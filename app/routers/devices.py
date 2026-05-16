"""Endpoint POST /api/devices/register — port de cognipilot-remote.

Upsert idempotente: si existe deviceUuid, actualiza; si no, crea.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser
from app.models.usuario import Dispositivo
from app.schemas.dispositivo import (
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceShortResponse,
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
