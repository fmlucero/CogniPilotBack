"""Endpoints de empresas — port byte-a-byte de cognipilot-remote/app/api/empresas/*.

Solo admin_sistema. CUIT validado liviano (11 dígitos, sin módulo 11).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_session
from app.core.deps import require_roles
from app.models.empresa import Empresa
from app.models.eventos import EventoApp
from app.models.regla import Regla
from app.models.operacion import Ruta
from app.models.usuario import Dispositivo, Usuario
from app.schemas.empresa import (
    EmpresaCreateRequest,
    EmpresaDetailResponse,
    EmpresaKpi,
    EmpresaListResponse,
    EmpresaPatchRequest,
    EmpresaReglaSummary,
    EmpresaResponse,
    EmpresaRutaSummary,
    EmpresaUsuarioSummary,
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


@router.get("", dependencies=[Depends(admin_only)])
async def list_empresas(
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, list[dict]]:
    # Nota: no usamos response_model=EmpresaListResponse porque Pydantic v2
    # filtra el campo `_count` (los campos con underscore inicial se tratan
    # como privados). El dict ya viene formateado con la estructura correcta.
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


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/empresas/{id}/detalle — HU-33: vista completa de la empresa
#   Devuelve datos + usuarios (con connectionState) + rutas + reglas + kpi 7d.
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


@router.get("/{empresa_id}/detalle", response_model=EmpresaDetailResponse, dependencies=[Depends(admin_only)])
async def get_empresa_detalle(
    empresa_id: str,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> EmpresaDetailResponse:
    empresa = (
        await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    ).scalar_one_or_none()
    if empresa is None:
        raise HTTPException(status_code=404, detail="No encontrada")

    now = datetime.now(timezone.utc)

    # Usuarios + dispositivos (para connectionState y count)
    usuarios = (
        await db.execute(
            select(Usuario)
            .options(selectinload(Usuario.dispositivos))
            .where(Usuario.empresaId == empresa_id)
            .order_by(Usuario.activo.desc(), Usuario.rol.asc(), Usuario.nombre.asc())
        )
    ).scalars().all()

    usuarios_summary: list[EmpresaUsuarioSummary] = []
    for u in usuarios:
        if u.dispositivos:
            last_seen_dt = max(d.lastSeen for d in u.dispositivos)
            state = _connection_state(last_seen_dt, now)
            last_seen_ms: int | None = int(_aware_utc(last_seen_dt).timestamp() * 1000)
        else:
            state = "offline"
            last_seen_ms = None
        usuarios_summary.append(EmpresaUsuarioSummary(
            id=u.id,
            nombre=u.nombre,
            email=u.email,
            rol=u.rol.value,
            activo=u.activo,
            connectionState=state,
            lastSeen=last_seen_ms,
            dispositivos=len(u.dispositivos),
        ))

    # Rutas (próximas + recientes, sin paginación por ahora — son pocas)
    rutas = (
        await db.execute(
            select(Ruta)
            .where(Ruta.empresaId == empresa_id)
            .order_by(Ruta.fecha.desc())
            .limit(50)
        )
    ).scalars().all()
    rutas_summary = [
        EmpresaRutaSummary(id=r.id, nombre=r.nombre, fecha=r.fecha.isoformat())
        for r in rutas
    ]

    # Reglas
    reglas = (
        await db.execute(
            select(Regla)
            .where(Regla.empresaId == empresa_id)
            .order_by(Regla.activa.desc(), Regla.updatedAt.desc())
        )
    ).scalars().all()
    reglas_summary = [
        EmpresaReglaSummary(
            id=r.id,
            nombre=r.nombre,
            tipo=r.tipo.value,
            accion=r.accion.value,
            activa=r.activa,
            rutaId=r.rutaId,
            updatedAt=int(_aware_utc(r.updatedAt).timestamp() * 1000),
        )
        for r in reglas
    ]

    # KPI 7 días
    cutoff_7d = now - timedelta(days=7)
    cutoff_5m = now - timedelta(minutes=5)
    cutoff_24h = now - timedelta(hours=24)
    usuario_ids_subq = select(Usuario.id).where(Usuario.empresaId == empresa_id)

    kpi_row = (
        await db.execute(
            select(
                func.count(EventoApp.id),
                func.count(func.distinct(EventoApp.usuarioId)),
            )
            .where(EventoApp.ts >= cutoff_7d, EventoApp.usuarioId.in_(usuario_ids_subq))
        )
    ).one()
    events_total_7d = int(kpi_row[0] or 0)
    active_users_7d = int(kpi_row[1] or 0)

    devices_5m = (
        await db.execute(
            select(func.count(Dispositivo.id))
            .where(Dispositivo.lastSeen >= cutoff_5m, Dispositivo.usuarioId.in_(usuario_ids_subq))
        )
    ).scalar() or 0
    devices_24h = (
        await db.execute(
            select(func.count(Dispositivo.id))
            .where(Dispositivo.lastSeen >= cutoff_24h, Dispositivo.usuarioId.in_(usuario_ids_subq))
        )
    ).scalar() or 0

    return EmpresaDetailResponse(
        id=empresa.id,
        nombre=empresa.nombre,
        cuit=empresa.cuit,
        contacto=empresa.contacto,
        activa=empresa.activa,
        createdAt=int(_aware_utc(empresa.createdAt).timestamp() * 1000),
        usuarios=usuarios_summary,
        rutas=rutas_summary,
        reglas=reglas_summary,
        kpi=EmpresaKpi(
            events_total_7d=events_total_7d,
            active_users_7d=active_users_7d,
            devices_active_5m=int(devices_5m),
            devices_active_24h=int(devices_24h),
        ),
    )
