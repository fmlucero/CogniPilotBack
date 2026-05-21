"""Endpoints de usuarios — port byte-a-byte de cognipilot-remote/app/api/usuarios/*.

Reglas de acceso:
  - admin_sistema ve todo, crea cualquier rol/empresa
  - supervisor ve solo su empresa, crea solo repartidores de su empresa
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_session
from app.core.deps import CurrentUser, require_roles
from app.core.security import hash_password
from app.models.empresa import Empresa
from app.models.enums import Rol
from app.models.eventos import EventoApp
from app.models.operacion import Asignacion, Ruta
from app.models.usuario import Dispositivo, Usuario
from app.schemas.usuario import (
    AsignacionSummary,
    ConnectionState,
    DispositivoSummary,
    EventoSummary,
    UsuarioCreateRequest,
    UsuarioCreateResponse,
    UsuarioDetailResponse,
    UsuarioListResponse,
    UsuarioPatchRequest,
    UsuarioResponse,
)
from app.utils.password import generate_temp_password

router = APIRouter(prefix="/api/usuarios", tags=["usuarios"])

auth_admin_or_super = require_roles("admin_sistema", "supervisor")


def _aware_utc(dt: datetime) -> datetime:
    """Normaliza a tz-aware UTC. Las columnas heredadas de Prisma vienen tz-naive
    aunque el modelo SQLAlchemy las declare como DateTime(timezone=True);
    asumimos UTC para los datos viejos (que es como Prisma los guardó)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _ms(dt: datetime) -> int:
    return int(_aware_utc(dt).timestamp() * 1000)


def _compute_connection_state(
    dispositivos: list[Dispositivo],
) -> tuple[ConnectionState, int | None]:
    """Estado de conexión derivado del max(lastSeen) entre todos los dispositivos del usuario."""
    if not dispositivos:
        return ConnectionState.offline, None
    last_seen = _aware_utc(max(d.lastSeen for d in dispositivos))
    now = datetime.now(timezone.utc)
    delta = now - last_seen
    if delta < timedelta(minutes=5):
        state = ConnectionState.online
    elif delta < timedelta(hours=24):
        state = ConnectionState.active_today
    else:
        state = ConnectionState.offline
    return state, int(last_seen.timestamp() * 1000)


def _to_response(u: Usuario, empresa_nombre: str | None) -> UsuarioResponse:
    state, last_seen = _compute_connection_state(u.dispositivos)
    return UsuarioResponse(
        id=u.id,
        email=u.email,
        nombre=u.nombre,
        rol=u.rol,
        empresaId=u.empresaId,
        empresaNombre=empresa_nombre,
        activo=u.activo,
        dispositivos=len(u.dispositivos),
        connectionState=state,
        lastSeen=last_seen,
        createdAt=_ms(u.createdAt),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/usuarios
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=UsuarioListResponse)
async def list_usuarios(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[Any, Depends(auth_admin_or_super)],
) -> dict[str, list[UsuarioResponse]]:
    stmt = (
        select(Usuario)
        .options(selectinload(Usuario.empresa), selectinload(Usuario.dispositivos))
        .order_by(Usuario.activo.desc(), Usuario.rol.asc(), Usuario.nombre.asc())
    )
    if current["rol"] == "supervisor":
        if not current["empresaId"]:
            return {"usuarios": []}
        stmt = stmt.where(Usuario.empresaId == current["empresaId"])

    usuarios = (await db.execute(stmt)).scalars().all()
    return {
        "usuarios": [
            _to_response(u, u.empresa.nombre if u.empresa else None)
            for u in usuarios
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/usuarios/{id}  — detalle (HU-22) + estado conexión (HU-23)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{usuario_id}", response_model=UsuarioDetailResponse)
async def get_usuario(
    usuario_id: str,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[Any, Depends(auth_admin_or_super)],
) -> UsuarioDetailResponse:
    target = (
        await db.execute(
            select(Usuario)
            .options(
                selectinload(Usuario.empresa),
                selectinload(Usuario.dispositivos),
            )
            .where(Usuario.id == usuario_id)
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Usuario no existe")

    # Supervisor solo ve usuarios de su empresa
    if current["rol"] == "supervisor" and target.empresaId != current["empresaId"]:
        raise HTTPException(status_code=403, detail="Sin permiso para ver este usuario")

    # Asignaciones: últimas 20, con ruta
    asignaciones_rows = (
        await db.execute(
            select(Asignacion, Ruta)
            .join(Ruta, Asignacion.rutaId == Ruta.id)
            .where(Asignacion.repartidorId == target.id)
            .order_by(Asignacion.fecha.desc())
            .limit(20)
        )
    ).all()

    # Eventos recientes: últimos 30
    eventos = (
        (
            await db.execute(
                select(EventoApp)
                .where(EventoApp.usuarioId == target.id)
                .order_by(EventoApp.ts.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )

    state, last_seen = _compute_connection_state(target.dispositivos)

    return UsuarioDetailResponse(
        id=target.id,
        email=target.email,
        nombre=target.nombre,
        rol=target.rol,
        empresaId=target.empresaId,
        empresaNombre=target.empresa.nombre if target.empresa else None,
        activo=target.activo,
        connectionState=state,
        lastSeen=last_seen,
        createdAt=_ms(target.createdAt),
        dispositivos=[
            DispositivoSummary(
                id=d.id,
                deviceUuid=d.deviceUuid,
                modelo=d.modelo,
                osVersion=d.osVersion,
                appVersion=d.appVersion,
                activo=d.activo,
                lastSeen=_ms(d.lastSeen),
                lastLat=float(d.lastLat) if d.lastLat is not None else None,
                lastLng=float(d.lastLng) if d.lastLng is not None else None,
                createdAt=_ms(d.createdAt),
            )
            for d in sorted(target.dispositivos, key=lambda x: x.lastSeen, reverse=True)
        ],
        asignaciones=[
            AsignacionSummary(
                id=a.id,
                rutaId=r.id,
                rutaNombre=r.nombre,
                fecha=a.fecha.isoformat(),
            )
            for a, r in asignaciones_rows
        ],
        eventosRecientes=[
            EventoSummary(
                id=e.id,
                tipo=e.tipo,
                ts=_ms(e.ts),
                screenName=e.screenName,
                appPackage=e.appPackage,
                inSchedule=e.inSchedule,
            )
            for e in eventos
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/usuarios
# ─────────────────────────────────────────────────────────────────────────────


@router.post("", status_code=status.HTTP_201_CREATED, response_model=UsuarioCreateResponse)
async def create_usuario(
    body: UsuarioCreateRequest,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[Any, Depends(auth_admin_or_super)],
) -> UsuarioCreateResponse:
    email = body.email.lower()
    rol = body.rol
    empresa_id: str | None = body.empresaId

    if rol == Rol.admin_sistema:
        empresa_id = None
    else:
        if not empresa_id:
            raise HTTPException(status_code=422, detail="Empresa requerida para este rol")
        empresa = (
            await db.execute(select(Empresa).where(Empresa.id == empresa_id))
        ).scalar_one_or_none()
        if empresa is None:
            raise HTTPException(status_code=422, detail="Empresa no existe")
        if not empresa.activa:
            raise HTTPException(status_code=422, detail="Empresa inactiva")

    if current["rol"] == "supervisor":
        if rol != Rol.repartidor:
            raise HTTPException(
                status_code=403, detail="El supervisor solo puede crear repartidores"
            )
        if empresa_id != current["empresaId"]:
            raise HTTPException(
                status_code=403,
                detail="El supervisor solo puede crear usuarios en su empresa",
            )

    plain = body.password.strip() if body.password else None
    generated = False
    if not plain:
        plain = generate_temp_password(12)
        generated = True
    elif len(plain) < 8:
        raise HTTPException(
            status_code=422, detail="La contraseña debe tener al menos 8 caracteres"
        )

    pw_hash = hash_password(plain)

    usuario = Usuario(
        nombre=body.nombre,
        email=email,
        rol=rol,
        empresaId=empresa_id,
        passwordHash=pw_hash,
    )
    db.add(usuario)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "Ya existe un usuario con ese email", "conflict": "email"},
        ) from e
    await db.refresh(usuario, attribute_names=["empresa", "dispositivos"])

    return UsuarioCreateResponse(
        usuario=_to_response(usuario, usuario.empresa.nombre if usuario.empresa else None),
        tempPassword=plain,
        passwordGenerated=generated,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/usuarios/{id}
# ─────────────────────────────────────────────────────────────────────────────


@router.patch("/{usuario_id}")
async def patch_usuario(
    usuario_id: str,
    body: UsuarioPatchRequest,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[Any, Depends(auth_admin_or_super)],
) -> dict[str, Any]:
    target = (
        await db.execute(
            select(Usuario)
            .options(selectinload(Usuario.empresa), selectinload(Usuario.dispositivos))
            .where(Usuario.id == usuario_id)
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Usuario no existe")

    if current["rol"] == "supervisor":
        if target.rol != Rol.repartidor or target.empresaId != current["empresaId"]:
            raise HTTPException(
                status_code=403, detail="Sin permiso para modificar este usuario"
            )

    updates_applied = False
    temp_password: str | None = None
    generated = False

    if body.nombre is not None:
        target.nombre = body.nombre
        updates_applied = True

    if body.rol is not None:
        if current["rol"] == "supervisor":
            raise HTTPException(status_code=403, detail="Supervisor no puede cambiar roles")
        target.rol = body.rol
        updates_applied = True

    if "empresaId" in body.model_fields_set:
        if current["rol"] == "supervisor":
            raise HTTPException(
                status_code=403, detail="Supervisor no puede reasignar empresas"
            )
        if body.empresaId is None:
            final_rol = target.rol
            if final_rol != Rol.admin_sistema:
                raise HTTPException(
                    status_code=422,
                    detail="Solo admin_sistema puede no tener empresa",
                )
            target.empresaId = None
        else:
            empresa = (
                await db.execute(select(Empresa).where(Empresa.id == body.empresaId))
            ).scalar_one_or_none()
            if empresa is None:
                raise HTTPException(status_code=422, detail="Empresa no existe")
            target.empresaId = body.empresaId
        updates_applied = True

    if body.activo is not None:
        if body.activo is False and target.id == current["sub"]:
            raise HTTPException(
                status_code=400, detail="No podés desactivar tu propio usuario"
            )
        target.activo = body.activo
        updates_applied = True

    if body.password is not None:
        if len(body.password) < 8:
            raise HTTPException(
                status_code=422,
                detail="La contraseña debe tener al menos 8 caracteres",
            )
        target.passwordHash = hash_password(body.password)
        temp_password = body.password
        updates_applied = True
    elif body.resetPassword:
        temp_password = generate_temp_password(12)
        target.passwordHash = hash_password(temp_password)
        generated = True
        updates_applied = True

    if not updates_applied:
        raise HTTPException(status_code=400, detail="Nada para actualizar")

    await db.commit()
    await db.refresh(target, attribute_names=["empresa", "dispositivos"])

    return {
        "usuario": _to_response(
            target,
            target.empresa.nombre if target.empresa else None,
        ).model_dump(),
        "tempPassword": temp_password,
        "passwordGenerated": generated,
    }
