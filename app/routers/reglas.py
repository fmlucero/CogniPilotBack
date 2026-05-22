"""HU-04 — CRUD admin de reglas + historial de cambios.

GET    /api/reglas                  admin global, supervisor/gerente su empresa.
POST   /api/reglas                  admin/supervisor crean (scope empresa).
PATCH  /api/reglas/{id}             admin/supervisor editan; historia por campo.
DELETE /api/reglas/{id}             admin/supervisor borran (cascade en historial).
GET    /api/reglas/{id}/historial   admin/supervisor/gerente leen historial.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import log_audit
from app.core.db import get_session
from app.core.deps import CurrentUser
from app.models.empresa import Empresa
from app.models.enums import AccionRegla, TipoRegla
from app.models.operacion import Ruta
from app.models.regla import Regla, ReglaHistorial
from app.models.usuario import Usuario
from app.schemas.regla import (
    HistorialEntry,
    HistorialResponse,
    ReglaCreate,
    ReglaResponse,
    ReglaUpdate,
    ReglasListResponse,
)

router = APIRouter(prefix="/api/reglas", tags=["reglas"])


def _aware_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ms(dt: datetime) -> int:
    return int(_aware_utc(dt).timestamp() * 1000)


async def _serialize(db: AsyncSession, r: Regla) -> ReglaResponse:
    """Carga empresa/ruta nombres en una sola pasada."""
    empresa_nombre = None
    ruta_nombre = None
    if r.empresaId:
        empresa_nombre = (
            await db.execute(select(Empresa.nombre).where(Empresa.id == r.empresaId))
        ).scalar_one_or_none()
    if r.rutaId:
        ruta_nombre = (
            await db.execute(select(Ruta.nombre).where(Ruta.id == r.rutaId))
        ).scalar_one_or_none()
    return ReglaResponse(
        id=r.id,
        empresaId=r.empresaId,
        empresaNombre=empresa_nombre,
        rutaId=r.rutaId,
        rutaNombre=ruta_nombre,
        nombre=r.nombre,
        tipo=r.tipo.value,
        accion=r.accion.value,
        condicion=r.condicion,
        activa=r.activa,
        createdAt=_ms(r.createdAt),
        updatedAt=_ms(r.updatedAt),
    )


def _check_can_write(current: dict, empresa_id: str) -> None:
    """Admin escribe en cualquier empresa. Supervisor solo en la suya. Resto 403."""
    rol = current["rol"]
    if rol == "admin_sistema":
        return
    if rol == "supervisor" and current.get("empresaId") == empresa_id:
        return
    raise HTTPException(status_code=403, detail="Forbidden")


def _check_can_read(current: dict, empresa_id: str) -> None:
    """Admin lee todo. Supervisor/gerente solo su empresa. Repartidor 403."""
    rol = current["rol"]
    if rol == "admin_sistema":
        return
    if rol in ("supervisor", "gerente") and current.get("empresaId") == empresa_id:
        return
    raise HTTPException(status_code=403, detail="Forbidden")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/reglas
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=ReglasListResponse)
async def list_reglas(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    empresaId: Annotated[str | None, Query()] = None,
    activa: Annotated[bool | None, Query()] = None,
) -> ReglasListResponse:
    rol = current["rol"]
    if rol == "repartidor":
        raise HTTPException(status_code=403, detail="Forbidden")

    scope_empresa: str | None
    if rol == "admin_sistema":
        scope_empresa = empresaId
    else:
        if not current.get("empresaId"):
            raise HTTPException(status_code=403, detail="Usuario sin empresa")
        if empresaId and empresaId != current["empresaId"]:
            raise HTTPException(status_code=403, detail="Solo podés ver tu propia empresa")
        scope_empresa = current["empresaId"]

    stmt = select(Regla).order_by(Regla.updatedAt.desc())
    if scope_empresa is not None:
        stmt = stmt.where(Regla.empresaId == scope_empresa)
    if activa is not None:
        stmt = stmt.where(Regla.activa.is_(activa))

    reglas = (await db.execute(stmt)).scalars().all()
    return ReglasListResponse(
        reglas=[await _serialize(db, r) for r in reglas]
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/reglas
# ─────────────────────────────────────────────────────────────────────────────


@router.post("", response_model=ReglaResponse, status_code=201)
async def create_regla(
    body: ReglaCreate,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ReglaResponse:
    _check_can_write(current, body.empresaId)

    # Validar que empresa existe.
    empresa = (
        await db.execute(select(Empresa).where(Empresa.id == body.empresaId))
    ).scalar_one_or_none()
    if empresa is None:
        raise HTTPException(status_code=404, detail="Empresa no existe")

    # Validar que ruta (si vino) pertenece a la empresa.
    if body.rutaId:
        ruta = (
            await db.execute(select(Ruta).where(Ruta.id == body.rutaId))
        ).scalar_one_or_none()
        if ruta is None:
            raise HTTPException(status_code=404, detail="Ruta no existe")
        if ruta.empresaId != body.empresaId:
            raise HTTPException(status_code=422, detail="La ruta no pertenece a la empresa indicada")

    regla = Regla(
        empresaId=body.empresaId,
        rutaId=body.rutaId,
        nombre=body.nombre,
        tipo=TipoRegla(body.tipo),
        accion=AccionRegla(body.accion),
        condicion=body.condicion,
        activa=body.activa,
    )
    db.add(regla)
    await db.flush()
    await db.commit()
    await db.refresh(regla)

    log_audit(
        "regla_created",
        usuario_id=current["sub"],
        email=current.get("email"),
        regla_id=regla.id,
        empresa_id=regla.empresaId,
        tipo=regla.tipo.value,
        accion=regla.accion.value,
        activa=regla.activa,
    )

    return await _serialize(db, regla)


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/reglas/{id} — modifica + escribe ReglaHistorial por campo
# ─────────────────────────────────────────────────────────────────────────────


@router.patch("/{regla_id}", response_model=ReglaResponse)
async def update_regla(
    regla_id: str,
    body: ReglaUpdate,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ReglaResponse:
    regla = (
        await db.execute(select(Regla).where(Regla.id == regla_id))
    ).scalar_one_or_none()
    if regla is None:
        raise HTTPException(status_code=404, detail="Regla no existe")
    _check_can_write(current, regla.empresaId)

    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return await _serialize(db, regla)

    # Validar coherencia ruta-empresa si la ruta cambia.
    if "rutaId" in changes and changes["rutaId"] is not None:
        ruta = (
            await db.execute(select(Ruta).where(Ruta.id == changes["rutaId"]))
        ).scalar_one_or_none()
        if ruta is None:
            raise HTTPException(status_code=404, detail="Ruta no existe")
        if ruta.empresaId != regla.empresaId:
            raise HTTPException(status_code=422, detail="La ruta no pertenece a la empresa de la regla")

    # Aplicar cambios + escribir historial por campo modificado.
    for campo, valor_new in changes.items():
        valor_old = getattr(regla, campo)
        if campo == "tipo":
            valor_new_db = TipoRegla(valor_new)
            valor_old_repr = valor_old.value if valor_old is not None else None
            valor_new_repr = valor_new
        elif campo == "accion":
            valor_new_db = AccionRegla(valor_new)
            valor_old_repr = valor_old.value if valor_old is not None else None
            valor_new_repr = valor_new
        else:
            valor_new_db = valor_new
            valor_old_repr = valor_old
            valor_new_repr = valor_new
        if valor_old_repr == valor_new_repr:
            continue
        setattr(regla, campo, valor_new_db)
        # Wrap escalares en dict por la columna JSONB.
        db.add(ReglaHistorial(
            reglaId=regla.id,
            usuarioId=current["sub"],
            campo=campo,
            valorOld={"v": valor_old_repr},
            valorNew={"v": valor_new_repr},
        ))

    regla.updatedAt = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(regla)

    log_audit(
        "regla_updated",
        usuario_id=current["sub"],
        email=current.get("email"),
        regla_id=regla.id,
        empresa_id=regla.empresaId,
        campos_modificados=list(changes.keys()),
    )

    return await _serialize(db, regla)


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/reglas/{id}
# ─────────────────────────────────────────────────────────────────────────────


@router.delete("/{regla_id}", status_code=204)
async def delete_regla(
    regla_id: str,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    regla = (
        await db.execute(select(Regla).where(Regla.id == regla_id))
    ).scalar_one_or_none()
    if regla is None:
        raise HTTPException(status_code=404, detail="Regla no existe")
    _check_can_write(current, regla.empresaId)

    # Borrar historial primero (no hay ON DELETE CASCADE en el schema heredado).
    await db.execute(delete(ReglaHistorial).where(ReglaHistorial.reglaId == regla_id))
    await db.delete(regla)
    await db.commit()

    log_audit(
        "regla_deleted",
        usuario_id=current["sub"],
        email=current.get("email"),
        regla_id=regla_id,
        empresa_id=regla.empresaId,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/reglas/{id}/historial
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{regla_id}/historial", response_model=HistorialResponse)
async def get_historial(
    regla_id: str,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HistorialResponse:
    regla = (
        await db.execute(select(Regla).where(Regla.id == regla_id))
    ).scalar_one_or_none()
    if regla is None:
        raise HTTPException(status_code=404, detail="Regla no existe")
    _check_can_read(current, regla.empresaId)

    entries = (
        await db.execute(
            select(ReglaHistorial)
            .where(ReglaHistorial.reglaId == regla_id)
            .options(selectinload(ReglaHistorial.usuario))
            .order_by(ReglaHistorial.ts.desc())
        )
    ).scalars().all()

    return HistorialResponse(
        historial=[
            HistorialEntry(
                id=h.id,
                ts=_ms(h.ts),
                usuarioId=h.usuarioId,
                usuarioEmail=h.usuario.email if h.usuario else None,
                campo=h.campo,
                valorOld=(h.valorOld or {}).get("v") if isinstance(h.valorOld, dict) else h.valorOld,
                valorNew=(h.valorNew or {}).get("v") if isinstance(h.valorNew, dict) else h.valorNew,
            )
            for h in entries
        ]
    )
