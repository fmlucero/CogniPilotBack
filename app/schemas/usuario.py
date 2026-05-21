"""Schemas Pydantic v2 para usuarios (CRUD)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.models.enums import Rol, TipoEvento

_EMAIL_PATTERN = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"


class ConnectionState(str, Enum):
    online = "online"           # último lastSeen < 5 min
    active_today = "active_today"  # < 24 h
    offline = "offline"         # ≥ 24 h o sin dispositivos


class UsuarioResponse(BaseModel):
    id: str
    email: str
    nombre: str
    rol: Rol
    empresaId: str | None
    empresaNombre: str | None = None
    activo: bool
    dispositivos: int = 0
    connectionState: ConnectionState = ConnectionState.offline
    lastSeen: int | None = None  # ms epoch, derivado del max(dispositivos.lastSeen)
    createdAt: int  # ms epoch (compat con admin web)


class UsuarioListResponse(BaseModel):
    usuarios: list[UsuarioResponse]


class UsuarioCreateRequest(BaseModel):
    nombre: str = Field(min_length=2)
    email: str = Field(pattern=_EMAIL_PATTERN)
    rol: Rol
    empresaId: str | None = None
    password: str | None = None

    @field_validator("nombre")
    @classmethod
    def _trim(cls, v: str) -> str:
        return v.strip()


class UsuarioCreateResponse(BaseModel):
    usuario: UsuarioResponse
    tempPassword: str | None = None
    passwordGenerated: bool = False


class UsuarioPatchRequest(BaseModel):
    nombre: str | None = None
    rol: Rol | None = None
    empresaId: str | None = None
    activo: bool | None = None
    password: str | None = None
    resetPassword: bool | None = None

    @field_validator("nombre")
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Nombre inválido")
        return v


# ─── Detalle (HU-22) ────────────────────────────────────────────────────────


class DispositivoSummary(BaseModel):
    id: str
    deviceUuid: str
    modelo: str | None
    osVersion: str | None
    appVersion: str | None
    activo: bool
    lastSeen: int  # ms epoch
    lastLat: float | None = None
    lastLng: float | None = None
    createdAt: int


class AsignacionSummary(BaseModel):
    id: str
    rutaId: str
    rutaNombre: str
    fecha: str  # ISO YYYY-MM-DD


class EventoSummary(BaseModel):
    id: str
    tipo: TipoEvento
    ts: int  # ms epoch
    screenName: str | None
    appPackage: str | None
    inSchedule: bool | None


class UsuarioDetailResponse(BaseModel):
    id: str
    email: str
    nombre: str
    rol: Rol
    empresaId: str | None
    empresaNombre: str | None
    activo: bool
    connectionState: ConnectionState
    lastSeen: int | None
    createdAt: int
    dispositivos: list[DispositivoSummary]
    asignaciones: list[AsignacionSummary]
    eventosRecientes: list[EventoSummary]
