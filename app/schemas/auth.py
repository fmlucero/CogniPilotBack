"""Schemas Pydantic v2 para auth (login/logout/me/refresh)."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import Rol

# Misma regex que el back viejo (lib/cuit.ts del back Next, y el seed): permite
# emails con TLDs reserved como ".local" usados internamente por el TIF.
# Pydantic EmailStr es demasiado estricto.
_EMAIL_PATTERN = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"


class LoginRequest(BaseModel):
    """POST /api/auth/login"""
    email: str = Field(pattern=_EMAIL_PATTERN)
    password: str = Field(min_length=1)
    # Si viene de la app Android, registramos/actualizamos el dispositivo
    deviceUuid: str | None = None
    fcmToken: str | None = None
    modelo: str | None = None
    osVersion: str | None = None
    appVersion: str | None = None


class UserResponse(BaseModel):
    id: str
    email: str
    nombre: str
    rol: Rol
    empresaId: str | None


class LoginResponse(BaseModel):
    user: UserResponse
    dispositivoId: str | None
    accessToken: str
    refreshToken: str


class ImpersonatingInfo(BaseModel):
    """HU-34: presente en /me y en respuestas de impersonate cuando hay un admin
    enmascarado detrás del usuario visible."""
    adminId: str
    adminEmail: str


class MeResponse(BaseModel):
    user: UserResponse
    impersonating: ImpersonatingInfo | None = None


class RefreshRequest(BaseModel):
    """POST /api/auth/refresh — body es opcional (cookie como fallback)."""
    refreshToken: str | None = None


class RefreshResponse(BaseModel):
    accessToken: str
    refreshToken: str


class ImpersonateResponse(BaseModel):
    """HU-34 — POST /api/auth/impersonate/{user_id}"""
    user: UserResponse
    accessToken: str
    refreshToken: str
    impersonating: ImpersonatingInfo


class StopImpersonatingResponse(BaseModel):
    """HU-34 — POST /api/auth/stop-impersonating"""
    user: UserResponse
    accessToken: str
    refreshToken: str
