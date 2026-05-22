"""HU-25 — Reset de password vía admin (sin email server).

POST   /api/auth/reset-request               público; siempre 200 con mensaje
                                              genérico (no revela si el email
                                              existe).
GET    /api/admin/reset-requests             admin only; lista solicitudes
                                              pendientes (atendida_at IS NULL).
POST   /api/admin/reset-requests/{id}/resolver
                                              admin only; genera nuevo password
                                              random, lo aplica al Usuario que
                                              matchee el email, marca la
                                              solicitud como atendida, devuelve
                                              el password en claro UNA SOLA VEZ
                                              al admin para entrega manual.
                                              Si el email no matchea, igual se
                                              marca atendida (no había nada que
                                              resetear). Audit log siempre.
"""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit
from app.core.db import get_session
from app.core.deps import CurrentUser
from app.core.security import hash_password
from app.models.reset_password import ResetPasswordRequest
from app.models.usuario import Usuario

public_router = APIRouter(prefix="/api/auth", tags=["auth"])
admin_router = APIRouter(prefix="/api/admin/reset-requests", tags=["admin", "auth"])


_PASSWORD_ALPHABET = string.ascii_letters + string.digits
_PASSWORD_LEN = 12


def _generate_password() -> str:
    """Password aleatorio cripto seguro, longitud 12, alfanumérico (sin
    símbolos para que el admin pueda dictarlo por teléfono o mensaje sin
    confundir caracteres)."""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LEN))


# ─────────────────────────────────────────────────────────────────────────────
# Público
# ─────────────────────────────────────────────────────────────────────────────


class ResetRequestBody(BaseModel):
    # No usamos EmailStr para evitar dep extra `email-validator` y porque
    # ya no hacemos validación estricta acá: si el email no matchea, lo
    # confirmamos como atendido sin reset.
    email: str


class GenericResetResponse(BaseModel):
    ok: bool
    message: str


@public_router.post("/reset-request", response_model=GenericResetResponse)
async def request_reset(
    body: ResetRequestBody,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> GenericResetResponse:
    """Endpoint público. Siempre devuelve éxito (mensaje genérico) para no
    permitir enumeración de cuentas. La solicitud queda persistida y el
    admin decide qué hacer."""
    email = body.email.lower()
    db.add(ResetPasswordRequest(email=email))
    await db.commit()
    log_audit("reset_request_received", email=email)
    return GenericResetResponse(
        ok=True,
        message=(
            "Si el email pertenece a una cuenta del sistema, un administrador "
            "se va a contactar con vos para resetear la contraseña."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Admin
# ─────────────────────────────────────────────────────────────────────────────


class ResetRequestRow(BaseModel):
    id: str
    email: str
    ts: int                       # ms epoch
    atendidaAt: int | None
    atendidaPor: str | None
    atendidaPorEmail: str | None
    usuarioExiste: bool           # info para el admin: ¿este email matchea un Usuario?


class ResetRequestListResponse(BaseModel):
    requests: list[ResetRequestRow]


class ResolverResponse(BaseModel):
    ok: bool
    email: str
    nuevoPassword: str | None     # null si el email no matchea ningún usuario
    usuarioId: str | None
    mensaje: str


def _ms(dt: datetime) -> int:
    return int((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp() * 1000)


@admin_router.get("", response_model=ResetRequestListResponse)
async def list_reset_requests(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    soloPendientes: Annotated[bool, Query()] = True,
) -> ResetRequestListResponse:
    if current["rol"] != "admin_sistema":
        raise HTTPException(status_code=403, detail="Forbidden")

    stmt = select(ResetPasswordRequest).order_by(desc(ResetPasswordRequest.ts)).limit(200)
    if soloPendientes:
        stmt = stmt.where(ResetPasswordRequest.atendidaAt.is_(None))
    rows = (await db.execute(stmt)).scalars().all()

    emails = list({r.email for r in rows})
    user_emails: set[str] = set()
    atendido_ids: set[str] = set()
    if emails:
        existentes = (
            await db.execute(select(Usuario.email).where(Usuario.email.in_(emails)))
        ).scalars().all()
        user_emails = set(existentes)
    atendido_ids = {r.atendidaPor for r in rows if r.atendidaPor}
    atendido_map: dict[str, str] = {}
    if atendido_ids:
        emails_by_id = (
            await db.execute(select(Usuario.id, Usuario.email).where(Usuario.id.in_(atendido_ids)))
        ).all()
        atendido_map = {row.id: row.email for row in emails_by_id}

    return ResetRequestListResponse(
        requests=[
            ResetRequestRow(
                id=r.id,
                email=r.email,
                ts=_ms(r.ts),
                atendidaAt=_ms(r.atendidaAt) if r.atendidaAt else None,
                atendidaPor=r.atendidaPor,
                atendidaPorEmail=atendido_map.get(r.atendidaPor) if r.atendidaPor else None,
                usuarioExiste=r.email in user_emails,
            )
            for r in rows
        ]
    )


@admin_router.post("/{request_id}/resolver", response_model=ResolverResponse)
async def resolver_reset_request(
    request_id: str,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ResolverResponse:
    if current["rol"] != "admin_sistema":
        raise HTTPException(status_code=403, detail="Forbidden")

    req = (
        await db.execute(select(ResetPasswordRequest).where(ResetPasswordRequest.id == request_id))
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Solicitud no existe")
    if req.atendidaAt is not None:
        raise HTTPException(status_code=409, detail="La solicitud ya fue atendida")

    # Buscar el Usuario por email. Si no existe, igual marcamos atendida —
    # el admin verá usuarioExiste=false en la lista, pero quiere poder
    # "cerrar" la solicitud de un email inexistente sin generar password.
    user = (
        await db.execute(select(Usuario).where(Usuario.email == req.email))
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    nuevo_password: str | None = None

    if user is not None and user.activo:
        nuevo_password = _generate_password()
        user.passwordHash = hash_password(nuevo_password)

    req.atendidaPor = current["sub"]
    req.atendidaAt = now
    await db.commit()

    log_audit(
        "reset_password_resuelto",
        usuario_id=current["sub"],
        email=current.get("email"),
        target_email=req.email,
        target_id=user.id if user else None,
        usuario_existe=user is not None,
        password_generado=nuevo_password is not None,
        reset_request_id=request_id,
    )

    if nuevo_password is not None:
        mensaje = (
            "Nuevo password generado. Entregalo al usuario por canal seguro. "
            "NO va a aparecer de nuevo."
        )
    elif user is None:
        mensaje = "El email no matchea ningún usuario. Solicitud marcada como atendida sin reset."
    else:
        mensaje = "El usuario está inactivo. Solicitud marcada como atendida sin reset."

    return ResolverResponse(
        ok=True,
        email=req.email,
        nuevoPassword=nuevo_password,
        usuarioId=user.id if user else None,
        mensaje=mensaje,
    )
