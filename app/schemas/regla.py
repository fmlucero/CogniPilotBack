"""Schemas Pydantic v2 para CRUD de reglas (HU-04)."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TipoReglaLit = Literal[
    "paquete_fuera_parada",
    "ventana_horaria",
    "app_bloqueada_en_horario",
    "geofence",
    "acceso_operativo",
]
AccionReglaLit = Literal["bloquear", "alertar"]
ModoAccesoLit = Literal["app_trabajo", "kiosko"]

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class GeoCondicion(BaseModel):
    """Geocerca de la regla de acceso operativo (HU-53)."""
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: float = Field(gt=0, le=100_000)


class HorarioCondicion(BaseModel):
    """Ventana horaria permitida (HH:MM, hora local de la flota). HU-53."""
    model_config = ConfigDict(extra="forbid")

    desde: str
    hasta: str

    @field_validator("desde", "hasta")
    @classmethod
    def _valid_hhmm(cls, v: str) -> str:
        if not _HHMM_RE.match(v):
            raise ValueError("debe tener formato HH:MM (00:00–23:59)")
        return v


class AccesoOperativoCondicion(BaseModel):
    """Condición de una regla `acceso_operativo` (HU-53).

    Combina geocerca y/o horario (al menos uno) + un `modo` de bloqueo que la app
    Android consume para decidir el enforcement: `app_trabajo` cierra la app de
    trabajo con aviso (HU-54), `kiosko` mantiene el teléfono bloqueado (HU-59).
    """
    model_config = ConfigDict(extra="forbid")

    geo: GeoCondicion | None = None
    horario: HorarioCondicion | None = None
    modo: ModoAccesoLit = "app_trabajo"

    @model_validator(mode="after")
    def _al_menos_una(self) -> "AccesoOperativoCondicion":
        if self.geo is None and self.horario is None:
            raise ValueError("la condición debe especificar geocerca y/o horario")
        return self


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
