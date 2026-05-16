"""Schemas Pydantic v2 para auth (login/logout/me/refresh)."""
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import Rol


class LoginRequest(BaseModel):
    """POST /api/auth/login"""
    email: EmailStr
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


class MeResponse(BaseModel):
    user: UserResponse


class RefreshRequest(BaseModel):
    """POST /api/auth/refresh — body es opcional (cookie como fallback)."""
    refreshToken: str | None = None


class RefreshResponse(BaseModel):
    accessToken: str
    refreshToken: str
