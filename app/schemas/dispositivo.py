"""Schemas Pydantic v2 para dispositivos."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DeviceRegisterRequest(BaseModel):
    deviceUuid: str
    fcmToken: str | None = None
    modelo: str | None = None
    osVersion: str | None = None
    appVersion: str | None = None


class DeviceShortResponse(BaseModel):
    id: str
    deviceUuid: str


class DeviceRegisterResponse(BaseModel):
    dispositivo: DeviceShortResponse


# ─── HU-43 — pre-flight check de capabilities del device Android ─────────────


class CapabilitiesReport(BaseModel):
    """Reporte de permisos/estado de la app Android.

    Todos los flags son opcionales: si la app no puede determinar uno (ej.
    notifications en API <33) lo omite y queda como estaba. extra='allow' deja
    aceptar flags nuevas sin tocar el schema.
    """
    model_config = ConfigDict(extra="allow")

    overlay_ok: bool | None = None
    accessibility_ok: bool | None = None
    location_perm: bool | None = None
    notifications_perm: bool | None = None
    monitor_running: bool | None = None


class CapabilitiesPatchResponse(BaseModel):
    deviceUuid: str
    capabilities: dict
    capabilitiesUpdatedAt: int     # ms epoch


# ─── HU-35 / HU-43 — listado plano con capabilities ─────────────────────────


class DispositivoRow(BaseModel):
    id: str
    deviceUuid: str
    modelo: str | None
    osVersion: str | None
    appVersion: str | None
    activo: bool
    lastSeen: int                # ms epoch
    lastLat: float | None
    lastLng: float | None
    createdAt: int               # ms epoch
    connectionState: str         # online | active_today | offline
    usuarioId: str
    usuarioNombre: str
    usuarioEmail: str
    usuarioRol: str
    empresaId: str | None
    empresaNombre: str | None
    # HU-43
    capabilities: dict | None
    capabilitiesUpdatedAt: int | None    # ms epoch o null
    preflightStatus: str                 # ready | not_ready | unknown


class DispositivosListResponse(BaseModel):
    dispositivos: list[DispositivoRow]
