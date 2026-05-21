"""Endpoint POST /api/positions — reporte GPS del repartidor.

Diseño: el celu reporta cada N segundos (default 30s). El backend SOLO inserta
una fila nueva en `Posicion` si la coordenada difiere de la última en >10 metros
(haversine). Si difiere menos, solo actualiza Dispositivo.lastLat/lastLng/lastSeen.

Esto controla el crecimiento de la tabla cuando el repartidor está parado.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit
from app.core.db import get_session
from app.core.deps import CurrentUser
from app.models.eventos import Posicion
from app.models.usuario import Dispositivo
from app.schemas.posicion import PositionReportRequest, PositionReportResponse
from app.utils.geo import DEFAULT_POSITION_THRESHOLD_M, haversine_meters

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.post("", response_model=PositionReportResponse)
async def report_position(
    body: PositionReportRequest,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> PositionReportResponse:
    # 1) Resolver dispositivo y validar que pertenezca al usuario auth
    dev = (
        await db.execute(
            select(Dispositivo).where(
                Dispositivo.deviceUuid == body.deviceUuid,
                Dispositivo.usuarioId == current["sub"],
            )
        )
    ).scalar_one_or_none()
    if dev is None:
        raise HTTPException(
            status_code=404,
            detail="Dispositivo no encontrado o no pertenece al usuario",
        )

    # 2) Decidir si insertar en Posicion o solo actualizar lastLat/lastLng del dispositivo
    inserted = False
    if dev.lastLat is None or dev.lastLng is None:
        # Primera posición — siempre insertamos
        inserted = True
    else:
        dist_m = haversine_meters(dev.lastLat, dev.lastLng, body.lat, body.lng)
        if dist_m >= DEFAULT_POSITION_THRESHOLD_M:
            inserted = True

    # 3) Aplicar:
    now = datetime.now(timezone.utc)
    if body.ts is not None:
        ts = datetime.fromtimestamp(body.ts / 1000, tz=timezone.utc)
    else:
        ts = now

    if inserted:
        db.add(
            Posicion(
                repartidorId=current["sub"],
                dispositivoId=dev.id,
                ts=ts,
                lat=Decimal(body.lat),
                lng=Decimal(body.lng),
            )
        )

    # Siempre actualizamos el snapshot del dispositivo
    dev.lastLat = Decimal(body.lat)
    dev.lastLng = Decimal(body.lng)
    dev.lastSeen = now

    await db.commit()

    log_audit(
        "position_reported",
        usuario_id=current["sub"],
        email=current.get("email"),
        empresa_id=current.get("empresaId"),
        dispositivo_id=dev.id,
        lat=float(body.lat),
        lng=float(body.lng),
        inserted=inserted,
    )

    return PositionReportResponse(inserted=inserted, queuedJobId=None)
