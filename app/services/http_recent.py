"""HU-47 — Ring buffer Redis con las últimas N peticiones HTTP que recibió el back.

Patrón LPUSH + LTRIM para mantener un buffer de tamaño fijo. El middleware HTTP
(registrado en `app/main.py`) llama a `push()` después de cada respuesta. El
endpoint admin `GET /api/system/requests` consume con `recent()`.

Best-effort: si Redis está caído, no rompe la request (try/except + log).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.services.realtime import _get_redis

logger = logging.getLogger(__name__)

KEY = "system:http_recent"
MAX_ITEMS = 100


async def push(entry: dict[str, Any]) -> None:
    """Empuja una entry al tope del ring y trunca al límite."""
    try:
        redis = _get_redis()
        # Pipeline: LPUSH + LTRIM en una sola roundtrip.
        async with redis.pipeline(transaction=False) as pipe:
            pipe.lpush(KEY, json.dumps(entry, default=str))
            pipe.ltrim(KEY, 0, MAX_ITEMS - 1)
            await pipe.execute()
    except Exception as e:  # noqa: BLE001
        # No queremos que un Redis caído rompa el request handling.
        logger.warning("http_recent.push failed (degradado): %s", e)


async def recent(limit: int = MAX_ITEMS) -> list[dict[str, Any]]:
    """Lee las últimas `limit` entries (más reciente primero)."""
    limit = max(1, min(limit, MAX_ITEMS))
    try:
        redis = _get_redis()
        raw = await redis.lrange(KEY, 0, limit - 1)
        out: list[dict[str, Any]] = []
        for s in raw:
            try:
                out.append(json.loads(s))
            except json.JSONDecodeError:
                continue
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("http_recent.recent failed: %s", e)
        return []
