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
