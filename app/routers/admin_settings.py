"""HU-39 — Endpoints admin para settings globales del sistema.

GET   /api/admin/settings        listado con metadata + valor actual.
PATCH /api/admin/settings        bulk update {key: value, ...}; valida tipo
                                  contra el catálogo, escribe en DB y audit
                                  log por cambio.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit
from app.core.db import get_session
from app.core.deps import CurrentUser
from app.core.settings_catalog import SETTINGS_CATALOG, coerce
from app.models.system_setting import SystemSetting

router = APIRouter(prefix="/api/admin/settings", tags=["admin", "settings"])


class SettingsListResponse(BaseModel):
    settings: list[dict[str, Any]]


class SettingsPatchRequest(BaseModel):
    """Bulk patch: {key: value, ...}. Keys desconocidos → 422."""
    values: dict[str, Any]


async def _load_values(db: AsyncSession) -> dict[str, Any]:
    """Devuelve {key: value} con todos los settings persistidos. Las que no
    están en DB caen al `default` del catálogo en el caller."""
    rows = (await db.execute(select(SystemSetting.key, SystemSetting.value))).all()
    return {r.key: r.value for r in rows}


@router.get("", response_model=SettingsListResponse)
async def list_settings(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SettingsListResponse:
    if current["rol"] != "admin_sistema":
        raise HTTPException(status_code=403, detail="Forbidden")
    persisted = await _load_values(db)
    out = []
    for meta in SETTINGS_CATALOG.values():
        # Postgres JSONB devuelve dict/list/scalars; nuestro JSON tiene la
        # forma `{"v": <scalar>}` para evitar el quirk de escalares JSONB
        # (mismo wrap que usamos en ReglaHistorial — ver HU-04).
        raw = persisted.get(meta.key)
        current_value = (raw or {}).get("v") if isinstance(raw, dict) else meta.default
        if current_value is None:
            current_value = meta.default
        out.append(meta.to_dict(current_value))
    return SettingsListResponse(settings=out)


@router.patch("")
async def patch_settings(
    body: SettingsPatchRequest,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if current["rol"] != "admin_sistema":
        raise HTTPException(status_code=403, detail="Forbidden")
    if not body.values:
        raise HTTPException(status_code=400, detail="Nada para actualizar")

    persisted = await _load_values(db)
    changes_applied: list[dict[str, Any]] = []

    for key, raw_value in body.values.items():
        meta = SETTINGS_CATALOG.get(key)
        if meta is None:
            raise HTTPException(status_code=422, detail=f"setting desconocido: {key}")
        try:
            coerced = coerce(raw_value, meta.type)
        except (TypeError, ValueError) as e:
            raise HTTPException(
                status_code=422,
                detail=f"valor inválido para {key} (tipo {meta.type}): {e}",
            ) from e

        old_raw = persisted.get(key)
        old_value = (old_raw or {}).get("v") if isinstance(old_raw, dict) else meta.default
        if old_value == coerced:
            continue

        existing = (
            await db.execute(select(SystemSetting).where(SystemSetting.key == key))
        ).scalar_one_or_none()
        wrapped = {"v": coerced}
        if existing is None:
            db.add(SystemSetting(
                key=key,
                value=wrapped,
                updatedBy=current["sub"],
            ))
        else:
            existing.value = wrapped
            existing.updatedBy = current["sub"]
            existing.updatedAt = datetime.now(timezone.utc)

        changes_applied.append({"key": key, "old": old_value, "new": coerced})
        log_audit(
            "setting_changed",
            usuario_id=current["sub"],
            email=current.get("email"),
            setting_key=key,
            old=old_value,
            new=coerced,
            hot_reload=meta.hot_reload,
        )

    await db.commit()
    return {"updated": len(changes_applied), "changes": changes_applied}


# ─────────────────────────────────────────────────────────────────────────────
# Helper público para que otros módulos lean settings con cache simple.
# ─────────────────────────────────────────────────────────────────────────────


async def get_setting(db: AsyncSession, key: str) -> Any:
    """Lee un setting de DB o devuelve el default del catálogo si no existe."""
    meta = SETTINGS_CATALOG.get(key)
    if meta is None:
        raise KeyError(f"setting no catalogado: {key}")
    row = (
        await db.execute(select(SystemSetting.value).where(SystemSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        return meta.default
    if isinstance(row, dict) and "v" in row:
        return row["v"]
    return row
