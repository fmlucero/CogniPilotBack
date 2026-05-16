"""Endpoints de empresas — port byte-a-byte de cognipilot-remote/app/api/empresas/*.

Solo admin_sistema. CUIT validado liviano (11 dígitos, sin módulo 11).
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_roles
from app.models.empresa import Empresa
from app.models.regla import Regla
from app.models.operacion import Ruta
from app.models.usuario import Usuario
from app.schemas.empresa import (
    EmpresaCreateRequest,
    EmpresaListResponse,
    EmpresaPatchRequest,
    EmpresaResponse,
)

router = APIRouter(prefix="/api/empresas", tags=["empresas"])

# Dependencia compartida: todos los endpoints requieren admin_sistema
admin_only = require_roles("admin_sistema")


async def _empresa_with_counts(db: AsyncSession, empresa: Empresa) -> dict[str, Any]:
    """Serializa empresa + _count.usuarios/rutas/reglas."""
    counts_q = await db.execute(
        select(
            func.count(Usuario.id).label("usuarios"),
            select(func.count(Ruta.id)).where(Ruta.empresaId == empresa.id).scalar_subquery().label("rutas"),
            select(func.count(Regla.id)).where(Regla.empresaId == empresa.id).scalar_subquery().label("reglas"),
        ).where(Usuario.empresaId == empresa.id)
    )
    row = counts_q.one()
    return {
        "id": empresa.id,
        "nombre": empresa.nombre,
        "cuit": empresa.cuit,
        "contacto": empresa.contacto,
        "activa": empresa.activa,
        "createdAt": empresa.createdAt,
        "_count": {
            "usuarios": int(row.usuarios or 0),
            "rutas": int(row.rutas or 0),
            "reglas": int(row.reglas or 0),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/empresas
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=EmpresaListResponse, dependencies=[Depends(admin_only)])
async def list_empresas(
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, list[dict]]:
    stmt = select(Empresa).order_by(desc(Empresa.activa), Empresa.nombre.asc())
    empresas = (await db.execute(stmt)).scalars().all()
    serialized = [await _empresa_with_counts(db, e) for e in empresas]
    return {"empresas": serialized}


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/empresas
# ─────────────────────────────────────────────────────────────────────────────


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(admin_only)])
async def create_empresa(
    body: EmpresaCreateRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    contacto_dict = (
        body.contacto.model_dump(exclude_none=True) if body.contacto else None
    )
    empresa = Empresa(nombre=body.nombre, cuit=body.cuit, contacto=contacto_dict)
    db.add(empresa)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        # Detectar qué unique falló
        msg = str(e.orig).lower() if e.orig else ""
        field = "CUIT" if "cuit" in msg else "nombre" if "nombre" in msg else "campo único"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": f"Ya existe una empresa con ese {field}", "conflict": field},
        ) from e
    await db.refresh(empresa)
    return {"empresa": EmpresaResponse(
        id=empresa.id,
        nombre=empresa.nombre,
        cuit=empresa.cuit,
        contacto=empresa.contacto,
        activa=empresa.activa,
        createdAt=empresa.createdAt,
    ).model_dump(by_alias=True)}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/empresas/{id}
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{empresa_id}", dependencies=[Depends(admin_only)])
async def get_empresa(
    empresa_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    empresa = (
        await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    ).scalar_one_or_none()
    if empresa is None:
        raise HTTPException(status_code=404, detail="No encontrada")
    return {"empresa": await _empresa_with_counts(db, empresa)}


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/empresas/{id}
# ─────────────────────────────────────────────────────────────────────────────


@router.patch("/{empresa_id}", dependencies=[Depends(admin_only)])
async def patch_empresa(
    empresa_id: str,
    body: EmpresaPatchRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    empresa = (
        await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    ).scalar_one_or_none()
    if empresa is None:
        raise HTTPException(status_code=404, detail="No encontrada")

    updates_applied = False

    if body.nombre is not None:
        empresa.nombre = body.nombre
        updates_applied = True

    if body.cuit is not None:
        empresa.cuit = body.cuit
        updates_applied = True

    if body.contacto is not None:
        empresa.contacto = body.contacto.model_dump(exclude_none=True)
        updates_applied = True
    elif "contacto" in body.model_fields_set:
        # explicit null
        empresa.contacto = None
        updates_applied = True

    if body.activa is not None:
        empresa.activa = body.activa
        updates_applied = True

    if not updates_applied:
        raise HTTPException(status_code=400, detail="Nada para actualizar")

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        msg = str(e.orig).lower() if e.orig else ""
        field = "CUIT" if "cuit" in msg else "nombre"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": f"Ya existe otra empresa con ese {field}", "conflict": field},
        ) from e
    await db.refresh(empresa)
    return {"empresa": await _empresa_with_counts(db, empresa)}
