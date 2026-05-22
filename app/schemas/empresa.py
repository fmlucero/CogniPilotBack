"""Schemas Pydantic v2 para empresas (CRUD)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.utils.cuit import is_valid_cuit, format_cuit


class Contacto(BaseModel):
    # Acepta cualquier string — es metadata de la empresa, validado livianamente.
    email: str | None = None
    telefono: str | None = None
    direccion: str | None = None


class EmpresaCount(BaseModel):
    usuarios: int
    rutas: int
    reglas: int


class EmpresaResponse(BaseModel):
    id: str
    nombre: str
    cuit: str
    contacto: dict | None
    activa: bool
    createdAt: datetime
    _count: EmpresaCount | None = None

    model_config = {"populate_by_name": True}


class EmpresaListResponse(BaseModel):
    empresas: list[EmpresaResponse]


class EmpresaCreateRequest(BaseModel):
    nombre: str = Field(min_length=2)
    cuit: str
    contacto: Contacto | None = None

    @field_validator("nombre")
    @classmethod
    def _trim_nombre(cls, v: str) -> str:
        return v.strip()

    @field_validator("cuit")
    @classmethod
    def _validate_cuit(cls, v: str) -> str:
        if not is_valid_cuit(v):
            raise ValueError("CUIT inválido")
        return format_cuit(v)


class EmpresaPatchRequest(BaseModel):
    nombre: str | None = None
    cuit: str | None = None
    contacto: Contacto | None = None
    activa: bool | None = None
    umbralErroresJornada: int | None = Field(default=None, ge=1, le=99)

    @field_validator("nombre")
    @classmethod
    def _trim_nombre(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Nombre inválido")
        return v

    @field_validator("cuit")
    @classmethod
    def _validate_cuit(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not is_valid_cuit(v):
            raise ValueError("CUIT inválido")
        return format_cuit(v)


# ─── HU-33 — detalle completo de empresa ─────────────────────────────────────


class EmpresaUsuarioSummary(BaseModel):
    id: str
    nombre: str
    email: str
    rol: str
    activo: bool
    connectionState: str          # online | active_today | offline
    lastSeen: int | None          # ms epoch
    dispositivos: int


class EmpresaRutaSummary(BaseModel):
    id: str
    nombre: str
    fecha: str                    # ISO YYYY-MM-DD


class EmpresaReglaSummary(BaseModel):
    id: str
    nombre: str
    tipo: str
    accion: str
    activa: bool
    rutaId: str | None
    updatedAt: int                # ms epoch


class EmpresaKpi(BaseModel):
    events_total_7d: int
    active_users_7d: int
    devices_active_5m: int
    devices_active_24h: int


class EmpresaDetailResponse(BaseModel):
    id: str
    nombre: str
    cuit: str
    contacto: dict | None
    activa: bool
    createdAt: int                # ms epoch
    usuarios: list[EmpresaUsuarioSummary]
    rutas: list[EmpresaRutaSummary]
    reglas: list[EmpresaReglaSummary]
    kpi: EmpresaKpi
