"""Audit log estructurado — HU-31.

Cada ingesta (evento, posición, login) emite una línea JSON-parseable en el
logger `cognipilot.audit`. El formato es:

    [INFO] cognipilot.audit: <event_name> {"usuario_id": "...", "email": "...", ...}

Esto permite:
  - `docker compose logs back-api | grep cognipilot.audit` para auditoría por usuario.
  - `... | grep event_ingested` para una acción específica.
  - parseo automático con `jq` o pipeline a Loki/Elastic si en el futuro queremos
    pasar logs a un sistema externo.

No requiere dependencias extra — usamos `json.dumps` con `default=str` para
serializar UUIDs, datetimes, Decimals, etc.
"""
from __future__ import annotations

import json
import logging
from typing import Any

audit_logger = logging.getLogger("cognipilot.audit")


def log_audit(event: str, /, **fields: Any) -> None:
    """Emite una línea de audit en formato `<event> <json>`.

    Args:
        event: nombre del evento de auditoría (ej: 'event_ingested', 'login_ok').
        **fields: campos arbitrarios — uuids, emails, ids, ts, etc.

    No re-raisea si el logging falla (es best-effort por diseño).
    """
    try:
        audit_logger.info("%s %s", event, json.dumps(fields, default=str, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        audit_logger.exception("audit log emit failed for event=%s", event)
