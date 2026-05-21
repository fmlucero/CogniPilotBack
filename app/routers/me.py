"""Endpoints /api/me/* — vista personalizada del usuario auth (repartidor).

GET /api/me/ruta?fecha=YYYY-MM-DD  → ruta asignada al repartidor para esa fecha (default: hoy)
GET /api/me/reglas                  → reglas activas aplicables a su empresa (y opcionalmente su ruta)

Ambos requieren Bearer válido. Solo rol=repartidor accede.
"""
from __future__ import annotations

from datetime import date as Date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_session
from app.core.deps import CurrentUser
from app.core.security import hash_password, verify_password
from app.models.operacion import Asignacion, Paquete, Parada, Ruta
from app.models.regla import Regla
from app.models.usuario import Usuario
from app.schemas.me import (
    ChangePasswordRequest,
    MiRutaResponse,
    MisReglasResponse,
    PaqueteOut,
    ParadaOut,
    ReglaOut,
    RutaOut,
)

router = APIRouter(prefix="/api/me", tags=["me"])

# TZ donde opera la flota (no usamos UTC para "hoy" porque el día operativo es local)
_FLEET_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _today_local() -> Date:
    return datetime.now(_FLEET_TZ).date()


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/me/ruta
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/ruta", response_model=MiRutaResponse)
async def my_route(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    fecha: Annotated[Date | None, Query(description="Fecha YYYY-MM-DD; default = hoy local")] = None,
) -> MiRutaResponse:
    if current["rol"] != "repartidor":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo repartidores")

    target = fecha or _today_local()

    asign = (
        await db.execute(
            select(Asignacion).where(
                and_(
                    Asignacion.repartidorId == current["sub"],
                    Asignacion.fecha == target,
                )
            )
        )
    ).scalar_one_or_none()

    if asign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sin asignación para {target.isoformat()}",
        )

    ruta = (
        await db.execute(
            select(Ruta)
            .where(Ruta.id == asign.rutaId)
            .options(selectinload(Ruta.paradas).selectinload(Parada.paquetes))
        )
    ).scalar_one()

    paradas_sorted = sorted(ruta.paradas, key=lambda p: p.orden)

    return MiRutaResponse(
        ruta=RutaOut(
            id=ruta.id,
            nombre=ruta.nombre,
            fecha=ruta.fecha,
            empresaId=ruta.empresaId,
        ),
        paradas=[
            ParadaOut(
                id=p.id,
                orden=p.orden,
                lat=p.lat,
                lng=p.lng,
                direccion=p.direccion,
                ventanaDesde=p.ventanaDesde,
                ventanaHasta=p.ventanaHasta,
                paquetes=[
                    PaqueteOut(id=pq.id, codigoMl=pq.codigoMl, descripcion=pq.descripcion)
                    for pq in p.paquetes
                ],
            )
            for p in paradas_sorted
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/me/reglas
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/reglas", response_model=MisReglasResponse)
async def my_rules(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> MisReglasResponse:
    if current["rol"] != "repartidor":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo repartidores")

    # Necesitamos la empresaId del usuario; viene en el JWT pero por las dudas
    # validamos contra DB (puede estar revocado/cambiado).
    user = (
        await db.execute(select(Usuario).where(Usuario.id == current["sub"]))
    ).scalar_one_or_none()
    if user is None or not user.activo or user.empresaId is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario sin empresa")

    # Reglas activas: las de la empresa, ya sea generales (rutaId NULL) o de alguna ruta concreta.
    # En el motor de reglas (HU-04+) las de ruta solo aplicarán si el repartidor está asignado
    # a esa ruta; por ahora mandamos todas las de la empresa y la app decide aplicación.
    reglas = (
        await db.execute(
            select(Regla)
            .where(
                and_(
                    Regla.empresaId == user.empresaId,
                    Regla.activa.is_(True),
                )
            )
            .order_by(Regla.createdAt.desc())
        )
    ).scalars().all()

    return MisReglasResponse(
        reglas=[
            ReglaOut(
                id=r.id,
                nombre=r.nombre,
                tipo=r.tipo.value,
                accion=r.accion.value,
                condicion=r.condicion,
                activa=r.activa,
                rutaId=r.rutaId,
            )
            for r in reglas
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/me/password — cambiar password propia (HU-24)
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_my_password(
    body: ChangePasswordRequest,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Cualquier rol autenticado puede cambiar su propia contraseña.

    Validación:
      - currentPassword debe matchear el hash actual (bcrypt)
      - newPassword ≥ 8 chars y distinta de la actual
    """
    if len(body.newPassword) < 8:
        raise HTTPException(
            status_code=422, detail="La nueva contraseña debe tener al menos 8 caracteres"
        )
    if body.newPassword == body.currentPassword:
        raise HTTPException(
            status_code=400, detail="La nueva contraseña debe ser distinta a la actual"
        )

    user = (
        await db.execute(select(Usuario).where(Usuario.id == current["sub"]))
    ).scalar_one_or_none()
    if user is None or not user.activo:
        raise HTTPException(status_code=403, detail="Usuario no válido")

    if not verify_password(body.currentPassword, user.passwordHash):
        raise HTTPException(status_code=400, detail="Contraseña actual incorrecta")

    user.passwordHash = hash_password(body.newPassword)
    await db.commit()
