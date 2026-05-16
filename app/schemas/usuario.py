"""Schemas Pydantic v2 para usuarios (CRUD)."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.models.enums import Rol

_EMAIL_PATTERN = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"


class UsuarioResponse(BaseModel):
    id: str
    email: str
    nombre: str
    rol: Rol
    empresaId: str | None
    empresaNombre: str | None = None
    activo: bool
    dispositivos: int = 0
    createdAt: int  # ms epoch (compat con admin web)


class UsuarioListResponse(BaseModel):
    usuarios: list[UsuarioResponse]


class UsuarioCreateRequest(BaseModel):
    nombre: str = Field(min_length=2)
    email: str = Field(pattern=_EMAIL_PATTERN)
    rol: Rol
    empresaId: str | None = None
    password: str | None = None  # si no viene, se autogenera

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
