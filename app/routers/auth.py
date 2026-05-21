"""Endpoints de auth — port byte-a-byte de cognipilot-remote/app/api/auth/*.

POST   /api/auth/login    público
POST   /api/auth/logout   público (idempotente)
GET    /api/auth/me       requiere auth
POST   /api/auth/refresh  con cookie o body
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit
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
from app.models.enums import Rol
from app.schemas.auth import (
    ImpersonateResponse,
    ImpersonatingInfo,
    LoginRequest,
    LoginResponse,
    MeResponse,
    RefreshRequest,
    RefreshResponse,
    StopImpersonatingResponse,
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


def _client_ip(request: Request) -> str | None:
    """Resolver IP del cliente respetando X-Forwarded-For (nginx adelante)."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> LoginResponse:
    email = body.email.lower()
    ip = _client_ip(request)

    stmt = select(Usuario).where(Usuario.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if user is None or not user.activo:
        log_audit("login_failed", email=email, reason="user_not_found_or_inactive", ip=ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not verify_password(body.password, user.passwordHash):
        log_audit("login_failed", email=email, usuario_id=user.id, reason="bad_password", ip=ip)
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

    log_audit(
        "login_ok",
        usuario_id=user.id,
        email=user.email,
        rol=user.rol.value,
        empresa_id=user.empresaId,
        dispositivo_id=dispositivo_id,
        ip=ip,
    )

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

    impersonating: ImpersonatingInfo | None = None
    admin_id = current.get("impersonated_by")
    if admin_id:
        admin = (
            await db.execute(select(Usuario).where(Usuario.id == admin_id))
        ).scalar_one_or_none()
        if admin is not None:
            impersonating = ImpersonatingInfo(adminId=admin.id, adminEmail=admin.email)

    return MeResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            nombre=user.nombre,
            rol=user.rol,
            empresaId=user.empresaId,
        ),
        impersonating=impersonating,
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

    # HU-34: preservar el claim impersonated_by entre refreshes para no
    # romper la sesión de impersonación cuando el access token vence.
    impersonated_by = payload.get("impersonated_by")
    new_access = sign_access(
        user.id, user.email, user.rol.value, user.empresaId,
        impersonated_by=impersonated_by,
    )
    new_refresh = sign_refresh(user.id, impersonated_by=impersonated_by)

    _set_auth_cookies(response, new_access, new_refresh)

    return RefreshResponse(accessToken=new_access, refreshToken=new_refresh)


# ─────────────────────────────────────────────────────────────────────────────
# HU-34 — Impersonación admin → supervisor/gerente
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/impersonate/{user_id}", response_model=ImpersonateResponse)
async def impersonate(
    user_id: str,
    current: CurrentUser,
    response: Response,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ImpersonateResponse:
    """Sólo admin_sistema puede impersonar. Target debe ser supervisor o
    gerente (jamás otro admin ni un repartidor). No se puede anidar."""
    if current["rol"] != "admin_sistema":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo admin_sistema puede impersonar")
    if current.get("impersonated_by"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya hay una impersonación activa; salí antes de iniciar otra")

    admin_id = current["sub"]
    if user_id == admin_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No podés impersonarte a vos mismo")

    target = (
        await db.execute(select(Usuario).where(Usuario.id == user_id))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    if not target.activo:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Usuario inactivo")
    if target.rol not in (Rol.supervisor, Rol.gerente):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Solo se puede impersonar supervisores o gerentes",
        )

    access = sign_access(
        target.id, target.email, target.rol.value, target.empresaId,
        impersonated_by=admin_id,
    )
    refresh = sign_refresh(target.id, impersonated_by=admin_id)
    _set_auth_cookies(response, access, refresh)

    log_audit(
        "impersonation_start",
        admin_id=admin_id,
        admin_email=current.get("email"),
        target_id=target.id,
        target_email=target.email,
        target_rol=target.rol.value,
        ip=_client_ip(request),
    )

    return ImpersonateResponse(
        user=UserResponse(
            id=target.id,
            email=target.email,
            nombre=target.nombre,
            rol=target.rol,
            empresaId=target.empresaId,
        ),
        accessToken=access,
        refreshToken=refresh,
        impersonating=ImpersonatingInfo(adminId=admin_id, adminEmail=current.get("email") or ""),
    )


@router.post("/stop-impersonating", response_model=StopImpersonatingResponse)
async def stop_impersonating(
    current: CurrentUser,
    response: Response,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> StopImpersonatingResponse:
    """Cierra la sesión impersonada y restaura tokens del admin original."""
    admin_id = current.get("impersonated_by")
    if not admin_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No hay una sesión de impersonación activa")

    admin = (
        await db.execute(select(Usuario).where(Usuario.id == admin_id))
    ).scalar_one_or_none()
    if admin is None or not admin.activo:
        # Caso patológico: el admin fue desactivado mientras impersonaba.
        # Limpiamos las cookies y forzamos re-login.
        response.delete_cookie(key=ACCESS_COOKIE, path="/")
        response.delete_cookie(key=REFRESH_COOKIE, path="/")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin original no disponible — re-loguear")

    access = sign_access(admin.id, admin.email, admin.rol.value, admin.empresaId)
    refresh = sign_refresh(admin.id)
    _set_auth_cookies(response, access, refresh)

    log_audit(
        "impersonation_stop",
        admin_id=admin.id,
        admin_email=admin.email,
        target_id=current["sub"],
        target_email=current.get("email"),
        ip=_client_ip(request),
    )

    return StopImpersonatingResponse(
        user=UserResponse(
            id=admin.id,
            email=admin.email,
            nombre=admin.nombre,
            rol=admin.rol,
            empresaId=admin.empresaId,
        ),
        accessToken=access,
        refreshToken=refresh,
    )
