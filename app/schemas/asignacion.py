"""Schemas Pydantic v2 para asignación de rutas a repartidores (HU-52)."""
from __future__ import annotations

from datetime import date as Date

from pydantic import BaseModel, ConfigDict


class AsignacionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rutaId: str
    repartidorId: str
    fecha: Date


class AsignacionResponse(BaseModel):
    id: str
    rutaId: str
    rutaNombre: str
    empresaId: str
    repartidorId: str
    repartidorNombre: str
    repartidorEmail: str
    fecha: Date


class AsignacionesListResponse(BaseModel):
    asignaciones: list[AsignacionResponse]


class RepartidorOption(BaseModel):
    id: str
    nombre: str
    email: str


class RepartidoresListResponse(BaseModel):
    repartidores: list[RepartidorOption]
