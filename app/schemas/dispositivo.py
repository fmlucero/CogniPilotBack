"""Schemas Pydantic v2 para dispositivos."""
from __future__ import annotations

from pydantic import BaseModel


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


# ─── HU-35 — listado plano de dispositivos para admin/supervisor ─────────────


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


class DispositivosListResponse(BaseModel):
    dispositivos: list[DispositivoRow]
