"""HU-52 — Asignación de rutas a repartidores por fecha.

GET    /api/asignaciones/repartidores   repartidores en scope (para el dropdown del panel).
GET    /api/asignaciones?rutaId=&repartidorId=   listado scoped.
POST   /api/asignaciones                 asigna repartidor + ruta + fecha.
DELETE /api/asignaciones/{id}            quita la asignación.

Scope: admin global, supervisor/gerente su empresa, repartidor 403. El repartidor
debe pertenecer a la misma empresa que la ruta. Unique (repartidorId, fecha) en DB
→ un repartidor tiene una sola ruta por día (409 si se viola).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit
from app.core.db import get_session
from app.core.deps import CurrentUser
from app.models.enums import Rol
from app.models.operacion import Asignacion, Ruta
from app.models.usuario import Usuario
from app.schemas.asignacion import (
    AsignacionCreate,
    AsignacionResponse,
    AsignacionesListResponse,
    RepartidorOption,
    RepartidoresListResponse,
)

router = APIRouter(prefix="/api/asignaciones", tags=["asignaciones"])


def _check_scope(current: dict, empresa_id: str) -> None:
    """Admin cualquier empresa; supervisor/gerente la suya; resto 403."""
    rol = current["rol"]
    if rol == "admin_sistema":
        return
    if rol in ("supervisor", "gerente") and current.get("empresaId") == empresa_id:
        return
    raise HTTPException(status_code=403, detail="Forbidden")


def _scope_empresa(current: dict, empresa_id_param: str | None) -> str | None:
    """Resuelve la empresa efectiva para listados según el rol.

    Devuelve el empresaId por el que filtrar (o None = sin filtro, sólo admin).
    """
    rol = current["rol"]
    if rol == "repartidor":
        raise HTTPException(status_code=403, detail="Forbidden")
    if rol == "admin_sistema":
        return empresa_id_param
    if not current.get("empresaId"):
        raise HTTPException(status_code=403, detail="Usuario sin empresa")
    if empresa_id_param and empresa_id_param != current["empresaId"]:
        raise HTTPException(status_code=403, detail="Solo podés ver tu propia empresa")
    return current["empresaId"]


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/asignaciones/repartidores — para el dropdown del panel de asignación
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/repartidores", response_model=RepartidoresListResponse)
async def list_repartidores(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    empresaId: Annotated[str | None, Query()] = None,
) -> RepartidoresListResponse:
    scope = _scope_empresa(current, empresaId)
    stmt = (
        select(Usuario)
        .where(Usuario.rol == Rol.repartidor, Usuario.activo.is_(True))
        .order_by(Usuario.nombre.asc())
    )
    if scope is not None:
        stmt = stmt.where(Usuario.empresaId == scope)
    repartidores = (await db.execute(stmt)).scalars().all()
    return RepartidoresListResponse(
        repartidores=[
            RepartidorOption(id=u.id, nombre=u.nombre, email=u.email) for u in repartidores
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/asignaciones
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=AsignacionesListResponse)
async def list_asignaciones(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    rutaId: Annotated[str | None, Query()] = None,
    repartidorId: Annotated[str | None, Query()] = None,
) -> AsignacionesListResponse:
    if current["rol"] == "repartidor":
        raise HTTPException(status_code=403, detail="Forbidden")

    stmt = (
        select(Asignacion, Ruta, Usuario)
        .join(Ruta, Asignacion.rutaId == Ruta.id)
        .join(Usuario, Asignacion.repartidorId == Usuario.id)
        .order_by(Asignacion.fecha.desc())
    )
    if current["rol"] != "admin_sistema":
        if not current.get("empresaId"):
            raise HTTPException(status_code=403, detail="Usuario sin empresa")
        stmt = stmt.where(Ruta.empresaId == current["empresaId"])
    if rutaId:
        stmt = stmt.where(Asignacion.rutaId == rutaId)
    if repartidorId:
        stmt = stmt.where(Asignacion.repartidorId == repartidorId)

    rows = (await db.execute(stmt)).all()
    return AsignacionesListResponse(
        asignaciones=[
            AsignacionResponse(
                id=a.id,
                rutaId=r.id,
                rutaNombre=r.nombre,
                empresaId=r.empresaId,
                repartidorId=u.id,
                repartidorNombre=u.nombre,
                repartidorEmail=u.email,
                fecha=a.fecha,
            )
            for a, r, u in rows
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/asignaciones
# ─────────────────────────────────────────────────────────────────────────────


@router.post("", response_model=AsignacionResponse, status_code=201)
async def create_asignacion(
    body: AsignacionCreate,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AsignacionResponse:
    ruta = (
        await db.execute(select(Ruta).where(Ruta.id == body.rutaId))
    ).scalar_one_or_none()
    if ruta is None:
        raise HTTPException(status_code=404, detail="Ruta no existe")
    _check_scope(current, ruta.empresaId)

    repartidor = (
        await db.execute(select(Usuario).where(Usuario.id == body.repartidorId))
    ).scalar_one_or_none()
    if repartidor is None:
        raise HTTPException(status_code=404, detail="Repartidor no existe")
    if repartidor.rol != Rol.repartidor:
        raise HTTPException(status_code=422, detail="El usuario no es un repartidor")
    if not repartidor.activo:
        raise HTTPException(status_code=422, detail="El repartidor está inactivo")
    if repartidor.empresaId != ruta.empresaId:
        raise HTTPException(
            status_code=422, detail="El repartidor no pertenece a la empresa de la ruta"
        )

    asign = Asignacion(repartidorId=body.repartidorId, rutaId=body.rutaId, fecha=body.fecha)
    db.add(asign)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="El repartidor ya tiene una ruta asignada para esa fecha",
        ) from e
    await db.refresh(asign)

    log_audit(
        "asignacion_created",
        usuario_id=current["sub"],
        email=current.get("email"),
        asignacion_id=asign.id,
        ruta_id=ruta.id,
        empresa_id=ruta.empresaId,
        repartidor_id=repartidor.id,
        fecha=body.fecha.isoformat(),
    )
    return AsignacionResponse(
        id=asign.id,
        rutaId=ruta.id,
        rutaNombre=ruta.nombre,
        empresaId=ruta.empresaId,
        repartidorId=repartidor.id,
        repartidorNombre=repartidor.nombre,
        repartidorEmail=repartidor.email,
        fecha=asign.fecha,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/asignaciones/{id}
# ─────────────────────────────────────────────────────────────────────────────


@router.delete("/{asignacion_id}", status_code=204)
async def delete_asignacion(
    asignacion_id: str,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    row = (
        await db.execute(
            select(Asignacion, Ruta)
            .join(Ruta, Asignacion.rutaId == Ruta.id)
            .where(Asignacion.id == asignacion_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Asignación no existe")
    asign, ruta = row
    _check_scope(current, ruta.empresaId)

    await db.delete(asign)
    await db.commit()

    log_audit(
        "asignacion_deleted",
        usuario_id=current["sub"],
        email=current.get("email"),
        asignacion_id=asignacion_id,
        ruta_id=ruta.id,
        empresa_id=ruta.empresaId,
        repartidor_id=asign.repartidorId,
    )
