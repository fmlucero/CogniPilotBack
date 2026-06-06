"""HU-50 — CRUD de rutas con paradas y paquetes anidados.

GET    /api/rutas                admin global; supervisor/gerente su empresa; repartidor 403.
POST   /api/rutas                admin/supervisor/gerente crean (scope empresa).
GET    /api/rutas/{id}           detalle con paradas + paquetes.
PATCH  /api/rutas/{id}           edita nombre/fecha; si viene `paradas`, reemplaza el set completo.
DELETE /api/rutas/{id}           borra ruta + paradas + paquetes (409 si tiene asignaciones o reglas).

El modelo Ruta/Parada/Paquete/Asignacion es heredado de Prisma (sin ON DELETE
CASCADE), por eso los borrados de hijos son explícitos.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import log_audit
from app.core.db import get_session
from app.core.deps import CurrentUser
from app.models.empresa import Empresa
from app.models.operacion import Asignacion, Paquete, Parada, Ruta
from app.models.regla import Regla
from app.schemas.ruta import (
    AsignadoItem,
    ParadaIn,
    ParadaResponse,
    PaqueteResponse,
    RutaCreate,
    RutaListItem,
    RutaResponse,
    RutasListResponse,
    RutaUpdate,
)

router = APIRouter(prefix="/api/rutas", tags=["rutas"])

_Q6 = Decimal("0.000001")


def _q6(value: Decimal) -> Decimal:
    """Cuantiza lat/lng a 6 decimales (la columna es Numeric(9,6))."""
    return value.quantize(_Q6, rounding=ROUND_HALF_UP)


def _check_scope(current: dict, empresa_id: str) -> None:
    """Admin opera en cualquier empresa; supervisor/gerente sólo en la suya.

    Repartidor (y cualquier otro) → 403. Misma regla para leer y escribir:
    la gestión de rutas es de admin + supervisor + gerente.
    """
    rol = current["rol"]
    if rol == "admin_sistema":
        return
    if rol in ("supervisor", "gerente") and current.get("empresaId") == empresa_id:
        return
    raise HTTPException(status_code=403, detail="Forbidden")


def _serialize_parada(p: Parada) -> ParadaResponse:
    return ParadaResponse(
        id=p.id,
        orden=p.orden,
        lat=p.lat,
        lng=p.lng,
        direccion=p.direccion,
        ventanaDesde=p.ventanaDesde,
        ventanaHasta=p.ventanaHasta,
        paquetes=[
            PaqueteResponse(id=pq.id, codigoMl=pq.codigoMl, descripcion=pq.descripcion)
            for pq in sorted(p.paquetes, key=lambda x: x.codigoMl)
        ],
    )


async def _load_detail(db: AsyncSession, ruta_id: str) -> RutaResponse:
    """Recarga la ruta con paradas+paquetes y empresa, y la serializa."""
    ruta = (
        await db.execute(
            select(Ruta)
            .where(Ruta.id == ruta_id)
            .options(
                selectinload(Ruta.paradas).selectinload(Parada.paquetes),
                selectinload(Ruta.empresa),
            )
        )
    ).scalar_one()
    paradas_sorted = sorted(ruta.paradas, key=lambda p: p.orden)
    paquetes_count = sum(len(p.paquetes) for p in paradas_sorted)
    return RutaResponse(
        id=ruta.id,
        empresaId=ruta.empresaId,
        empresaNombre=ruta.empresa.nombre if ruta.empresa else None,
        nombre=ruta.nombre,
        fecha=ruta.fecha,
        paradas=[_serialize_parada(p) for p in paradas_sorted],
        paquetesCount=paquetes_count,
    )


def _build_paradas(ruta_id: str, paradas: list[ParadaIn]) -> list[Parada]:
    """Construye objetos Parada (con sus Paquetes) desde el input."""
    out: list[Parada] = []
    for p in paradas:
        parada = Parada(
            rutaId=ruta_id,
            orden=p.orden,
            lat=_q6(p.lat),
            lng=_q6(p.lng),
            direccion=p.direccion,
            ventanaDesde=p.ventanaDesde,
            ventanaHasta=p.ventanaHasta,
        )
        parada.paquetes = [
            Paquete(codigoMl=pq.codigoMl, descripcion=pq.descripcion) for pq in p.paquetes
        ]
        out.append(parada)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/rutas
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=RutasListResponse)
async def list_rutas(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    empresaId: Annotated[str | None, Query()] = None,
) -> RutasListResponse:
    rol = current["rol"]
    if rol == "repartidor":
        raise HTTPException(status_code=403, detail="Forbidden")

    if rol == "admin_sistema":
        scope_empresa = empresaId
    else:
        if not current.get("empresaId"):
            raise HTTPException(status_code=403, detail="Usuario sin empresa")
        if empresaId and empresaId != current["empresaId"]:
            raise HTTPException(status_code=403, detail="Solo podés ver tu propia empresa")
        scope_empresa = current["empresaId"]

    stmt = (
        select(Ruta)
        .options(
            selectinload(Ruta.paradas).selectinload(Parada.paquetes),
            selectinload(Ruta.asignaciones).selectinload(Asignacion.repartidor),
            selectinload(Ruta.empresa),
        )
        .order_by(Ruta.fecha.desc(), Ruta.nombre.asc())
    )
    if scope_empresa is not None:
        stmt = stmt.where(Ruta.empresaId == scope_empresa)

    rutas = (await db.execute(stmt)).scalars().all()
    return RutasListResponse(
        rutas=[
            RutaListItem(
                id=r.id,
                empresaId=r.empresaId,
                empresaNombre=r.empresa.nombre if r.empresa else None,
                nombre=r.nombre,
                fecha=r.fecha,
                paradasCount=len(r.paradas),
                paquetesCount=sum(len(p.paquetes) for p in r.paradas),
                asignacionesCount=len(r.asignaciones),
                asignados=[
                    AsignadoItem(
                        repartidorId=a.repartidorId,
                        repartidorNombre=a.repartidor.nombre if a.repartidor else "—",
                        fecha=a.fecha,
                    )
                    for a in sorted(r.asignaciones, key=lambda a: a.fecha, reverse=True)
                ],
            )
            for r in rutas
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/rutas
# ─────────────────────────────────────────────────────────────────────────────


@router.post("", response_model=RutaResponse, status_code=201)
async def create_ruta(
    body: RutaCreate,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> RutaResponse:
    _check_scope(current, body.empresaId)

    empresa = (
        await db.execute(select(Empresa).where(Empresa.id == body.empresaId))
    ).scalar_one_or_none()
    if empresa is None:
        raise HTTPException(status_code=404, detail="Empresa no existe")

    ruta = Ruta(empresaId=body.empresaId, nombre=body.nombre, fecha=body.fecha)
    db.add(ruta)
    await db.flush()  # genera ruta.id para las paradas
    for parada in _build_paradas(ruta.id, body.paradas):
        db.add(parada)
    await db.commit()

    log_audit(
        "ruta_created",
        usuario_id=current["sub"],
        email=current.get("email"),
        ruta_id=ruta.id,
        empresa_id=ruta.empresaId,
        paradas=len(body.paradas),
        paquetes=sum(len(p.paquetes) for p in body.paradas),
    )
    return await _load_detail(db, ruta.id)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/rutas/{id}
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{ruta_id}", response_model=RutaResponse)
async def get_ruta(
    ruta_id: str,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> RutaResponse:
    ruta = (
        await db.execute(select(Ruta).where(Ruta.id == ruta_id))
    ).scalar_one_or_none()
    if ruta is None:
        raise HTTPException(status_code=404, detail="Ruta no existe")
    _check_scope(current, ruta.empresaId)
    return await _load_detail(db, ruta_id)


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/rutas/{id}
# ─────────────────────────────────────────────────────────────────────────────


@router.patch("/{ruta_id}", response_model=RutaResponse)
async def update_ruta(
    ruta_id: str,
    body: RutaUpdate,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> RutaResponse:
    ruta = (
        await db.execute(select(Ruta).where(Ruta.id == ruta_id))
    ).scalar_one_or_none()
    if ruta is None:
        raise HTTPException(status_code=404, detail="Ruta no existe")
    _check_scope(current, ruta.empresaId)

    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return await _load_detail(db, ruta_id)

    if "nombre" in changes and changes["nombre"] is not None:
        ruta.nombre = body.nombre
    if "fecha" in changes and changes["fecha"] is not None:
        ruta.fecha = body.fecha

    # Reemplazo completo del set de paradas (con sus paquetes) si vino `paradas`.
    if body.paradas is not None:
        parada_ids = (
            await db.execute(select(Parada.id).where(Parada.rutaId == ruta_id))
        ).scalars().all()
        if parada_ids:
            await db.execute(delete(Paquete).where(Paquete.paradaId.in_(parada_ids)))
            await db.execute(delete(Parada).where(Parada.id.in_(parada_ids)))
        await db.flush()
        for parada in _build_paradas(ruta_id, body.paradas):
            db.add(parada)

    await db.commit()

    log_audit(
        "ruta_updated",
        usuario_id=current["sub"],
        email=current.get("email"),
        ruta_id=ruta.id,
        empresa_id=ruta.empresaId,
        campos=list(changes.keys()),
    )
    return await _load_detail(db, ruta_id)


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/rutas/{id}
# ─────────────────────────────────────────────────────────────────────────────


@router.delete("/{ruta_id}", status_code=204)
async def delete_ruta(
    ruta_id: str,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    ruta = (
        await db.execute(select(Ruta).where(Ruta.id == ruta_id))
    ).scalar_one_or_none()
    if ruta is None:
        raise HTTPException(status_code=404, detail="Ruta no existe")
    _check_scope(current, ruta.empresaId)

    asign_count = (
        await db.execute(
            select(func.count(Asignacion.id)).where(Asignacion.rutaId == ruta_id)
        )
    ).scalar() or 0
    if asign_count:
        raise HTTPException(
            status_code=409,
            detail=f"La ruta tiene {asign_count} asignación(es); quitalas antes de borrarla",
        )

    regla_count = (
        await db.execute(
            select(func.count(Regla.id)).where(Regla.rutaId == ruta_id)
        )
    ).scalar() or 0
    if regla_count:
        raise HTTPException(
            status_code=409,
            detail=f"La ruta tiene {regla_count} regla(s) asociada(s); reasignalas antes de borrarla",
        )

    parada_ids = (
        await db.execute(select(Parada.id).where(Parada.rutaId == ruta_id))
    ).scalars().all()
    if parada_ids:
        await db.execute(delete(Paquete).where(Paquete.paradaId.in_(parada_ids)))
        await db.execute(delete(Parada).where(Parada.id.in_(parada_ids)))
    await db.delete(ruta)
    await db.commit()

    log_audit(
        "ruta_deleted",
        usuario_id=current["sub"],
        email=current.get("email"),
        ruta_id=ruta_id,
        empresa_id=ruta.empresaId,
    )
