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
