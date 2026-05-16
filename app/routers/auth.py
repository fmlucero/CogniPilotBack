"""Endpoints de auth — port byte-a-byte de cognipilot-remote/app/api/auth/*.

POST   /api/auth/login    público
POST   /api/auth/logout   público (idempotente)
GET    /api/auth/me       requiere auth
POST   /api/auth/refresh  con cookie o body
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import ACCESS_COOKIE, REFRESH_COOKIE, CurrentUser
from app.core.security import (
    sign_access,
    sign_refresh,
    verify_password,
    verify_refresh,
)
from app.models.usuario import Dispositivo, Usuario
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    MeResponse,
    RefreshRequest,
    RefreshResponse,
    UserResponse,
)
from fastapi import Depends

router = APIRouter(prefix="/api/auth", tags=["auth"])

_settings = get_settings()


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    """Set cp_at / cp_rt cookies — same names and options as cognipilot-remote."""
    response.set_cookie(
        key=ACCESS_COOKIE,
        value=access,
        max_age=_settings.access_token_ttl_min * 60,
        httponly=True,
        secure=_settings.cookie_secure,
        samesite="lax",
        path="/",
        domain=_settings.cookie_domain or None,
    )
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=refresh,
        max_age=_settings.refresh_token_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=_settings.cookie_secure,
        samesite="lax",
        path="/",
        domain=_settings.cookie_domain or None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/auth/login
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> LoginResponse:
    email = body.email.lower()

    stmt = select(Usuario).where(Usuario.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if user is None or not user.activo:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not verify_password(body.password, user.passwordHash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Auto-registrar/actualizar dispositivo si vino con deviceUuid (app Android)
    dispositivo_id: str | None = None
    if body.deviceUuid:
        existing = (
            await db.execute(
                select(Dispositivo).where(Dispositivo.deviceUuid == body.deviceUuid)
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.usuarioId = user.id
            existing.fcmToken = body.fcmToken
            existing.modelo = body.modelo
            existing.osVersion = body.osVersion
            existing.appVersion = body.appVersion
            existing.lastSeen = datetime.now()
            existing.activo = True
            dispositivo_id = existing.id
        else:
            new_dev = Dispositivo(
                usuarioId=user.id,
                deviceUuid=body.deviceUuid,
                fcmToken=body.fcmToken,
                modelo=body.modelo,
                osVersion=body.osVersion,
                appVersion=body.appVersion,
            )
            db.add(new_dev)
            await db.flush()
            dispositivo_id = new_dev.id
        await db.commit()

    access = sign_access(user.id, user.email, user.rol.value, user.empresaId)
    refresh = sign_refresh(user.id)

    _set_auth_cookies(response, access, refresh)

    return LoginResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            nombre=user.nombre,
            rol=user.rol,
            empresaId=user.empresaId,
        ),
        dispositivoId=dispositivo_id,
        accessToken=access,
        refreshToken=refresh,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/auth/logout — limpia cookies. Idempotente.
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(key=ACCESS_COOKIE, path="/")
    response.delete_cookie(key=REFRESH_COOKIE, path="/")
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/auth/me
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/me", response_model=MeResponse)
async def me(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    user = (
        await db.execute(select(Usuario).where(Usuario.id == current["sub"]))
    ).scalar_one_or_none()
    if user is None or not user.activo:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return MeResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            nombre=user.nombre,
            rol=user.rol,
            empresaId=user.empresaId,
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/auth/refresh
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    body: RefreshRequest | None,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_session)],
    cp_rt: Annotated[str | None, Cookie()] = None,
) -> RefreshResponse:
    token = (body.refreshToken if body else None) or cp_rt
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")

    payload = verify_refresh(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user = (
        await db.execute(select(Usuario).where(Usuario.id == payload["sub"]))
    ).scalar_one_or_none()
    if user is None or not user.activo:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    new_access = sign_access(user.id, user.email, user.rol.value, user.empresaId)
    new_refresh = sign_refresh(user.id)

    _set_auth_cookies(response, new_access, new_refresh)

    return RefreshResponse(accessToken=new_access, refreshToken=new_refresh)
