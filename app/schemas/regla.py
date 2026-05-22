"""Schemas Pydantic v2 para CRUD de reglas (HU-04)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TipoReglaLit = Literal["paquete_fuera_parada", "ventana_horaria", "app_bloqueada_en_horario"]
AccionReglaLit = Literal["bloquear", "alertar"]


class ReglaResponse(BaseModel):
    id: str
    empresaId: str
    empresaNombre: str | None
    rutaId: str | None
    rutaNombre: str | None
    nombre: str
    tipo: TipoReglaLit
    accion: AccionReglaLit
    condicion: dict[str, Any]
    activa: bool
    createdAt: int                # ms epoch
    updatedAt: int                # ms epoch


class ReglasListResponse(BaseModel):
    reglas: list[ReglaResponse]


class ReglaCreate(BaseModel):
    empresaId: str
    rutaId: str | None = None
    nombre: str = Field(min_length=1, max_length=200)
    tipo: TipoReglaLit
    accion: AccionReglaLit
    condicion: dict[str, Any] = Field(default_factory=dict)
    activa: bool = True


class ReglaUpdate(BaseModel):
    """Todos opcionales — los campos no enviados no se tocan ni se historizan."""
    model_config = ConfigDict(extra="forbid")

    rutaId: str | None = None
    nombre: str | None = None
    tipo: TipoReglaLit | None = None
    accion: AccionReglaLit | None = None
    condicion: dict[str, Any] | None = None
    activa: bool | None = None


class HistorialEntry(BaseModel):
    id: str
    ts: int                       # ms epoch
    usuarioId: str
    usuarioEmail: str | None
    campo: str
    valorOld: Any
    valorNew: Any


class HistorialResponse(BaseModel):
    historial: list[HistorialEntry]
