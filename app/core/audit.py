"""Audit log estructurado — HU-31 (stdout) + HU-36 (persistencia DB).

Cada llamada a `log_audit(event, **fields)`:
  1. emite una línea JSON-parseable en el logger `cognipilot.audit`
     (`[INFO] cognipilot.audit: <event> {...}`),
  2. dispara `asyncio.create_task(...)` para INSERT-ar el evento en la tabla
     `AuditEvent` (best-effort: si falla el INSERT, queda el stdout como
     fallback y no se propaga al request).

El mapeo kwargs → columnas reusa los nombres ya en uso en routers/auth.py:
    actor_id     ← admin_id | usuario_id | actor_id
    actor_email  ← admin_email | email | actor_email
    target_id    ← target_id            (solo presente en impersonate)
    target_email ← target_email
    ip           ← ip
Todo lo demás queda en `fields_json`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

audit_logger = logging.getLogger("cognipilot.audit")

_ACTOR_ID_KEYS = ("admin_id", "usuario_id", "actor_id")
_ACTOR_EMAIL_KEYS = ("admin_email", "email", "actor_email")
_TARGET_ID_KEYS = ("target_id",)
_TARGET_EMAIL_KEYS = ("target_email",)
_IP_KEYS = ("ip",)
# Eventos de alto volumen — se persisten igual, el filtro es en el endpoint.
TELEMETRY_EVENTS: frozenset[str] = frozenset(
    {"event_ingested", "events_bulk_ingested", "position_reported"}
)


def _pick(kwargs: dict[str, Any], keys: tuple[str, ...]) -> Any | None:
    for k in keys:
        v = kwargs.get(k)
        if v is not None:
            return v
    return None


def log_audit(event: str, /, **fields: Any) -> None:
    """Emite audit a stdout y dispara persistencia DB (best-effort).

    No-raise: si cualquier paso falla, se loggea con `audit_logger.exception` y
    el caller sigue sin ver el error.
    """
    # 1) stdout (HU-31)
    try:
        audit_logger.info("%s %s", event, json.dumps(fields, default=str, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        audit_logger.exception("audit log emit failed for event=%s", event)

    # 2) DB (HU-36) — fire-and-forget; no bloquea el request
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Caller no está dentro de un event loop (ej: scripts CLI) — skip DB.
        return
    loop.create_task(_persist(event, dict(fields)))


async def _persist(event: str, fields: dict[str, Any]) -> None:
    """Inserta una fila en AuditEvent con su propia session.

    Usa session nueva (no la del request) para que un rollback de negocio NO
    se lleve puesto el audit log.
    """
    try:
        # Import diferido: evita ciclo y permite que módulos sin DB importen audit.
        from app.core.db import SessionLocal
        from app.models.audit import AuditEvent

        actor_id = _pick(fields, _ACTOR_ID_KEYS)
        actor_email = _pick(fields, _ACTOR_EMAIL_KEYS)
        target_id = _pick(fields, _TARGET_ID_KEYS)
        target_email = _pick(fields, _TARGET_EMAIL_KEYS)
        ip = _pick(fields, _IP_KEYS)

        consumed = set(
            _ACTOR_ID_KEYS + _ACTOR_EMAIL_KEYS + _TARGET_ID_KEYS + _TARGET_EMAIL_KEYS + _IP_KEYS
        )
        extra = {k: v for k, v in fields.items() if k not in consumed}
        # Normalizar a JSON-serializable (UUID, datetime, Decimal → str).
        extra_safe = json.loads(json.dumps(extra, default=str, ensure_ascii=False)) if extra else None

        row = AuditEvent(
            event=event,
            actor_id=str(actor_id) if actor_id is not None else None,
            actor_email=actor_email,
            target_id=str(target_id) if target_id is not None else None,
            target_email=target_email,
            ip=ip,
            fields_json=extra_safe,
        )
        async with SessionLocal() as db:
            db.add(row)
            await db.commit()
    except Exception:  # noqa: BLE001
        audit_logger.exception("audit DB persist failed for event=%s", event)
