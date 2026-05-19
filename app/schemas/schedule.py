"""Schemas Pydantic v2 para schedule (ventana horaria)."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


class ScheduleResponse(BaseModel):
    """Forma `{ enabled, from, to, tz, updatedAt, updatedBy }` compat con app Android.

    `from` es palabra reservada en Python; Pydantic permite alias.
    """
    enabled: bool
    time_from: str | None = Field(default=None, alias="from")
    time_to: str | None = Field(default=None, alias="to")
    tz: str | None = None
    updatedAt: int | None = None  # ms epoch
    updatedBy: str | None = None

    model_config = {"populate_by_name": True}


class ScheduleUpdateRequest(BaseModel):
    enabled: bool
    time_from: str = Field(alias="from")
    time_to: str = Field(alias="to")
    tz: str

    model_config = {"populate_by_name": True}

    @field_validator("time_from", "time_to")
    @classmethod
    def _validate_hhmm(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError("Debe tener formato HH:mm")
        return v


class ScheduleUpdateResponse(ScheduleResponse):
    # Post HU-17 no hay campos extras; el back ya no dispara push.
    pass
