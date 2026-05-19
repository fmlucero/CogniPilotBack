"""Realtime pub/sub sobre Redis para HU-18 fase 4.

Reemplaza al broadcast FCM. Cuando algo cambia en el back (ej. supervisor
toggle horario), publishea a un channel Redis. Los clientes conectados al
endpoint SSE `/api/realtime/stream` reciben el evento en <100ms.

Diseño minimalista: un solo channel `realtime:schedule` por ahora. Cuando
se agreguen otros tipos (reglas, alertas, posiciones flota), van a channels
separados o se usa un único channel `realtime:events` con campo `type`.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

CHANNEL_SCHEDULE = "realtime:schedule"

_redis_singleton: Redis | None = None


def _get_redis() -> Redis:
    """Singleton del cliente Redis para pub/sub."""
    global _redis_singleton
    if _redis_singleton is None:
        _redis_singleton = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis_singleton


async def publish_schedule_updated(payload: dict[str, Any]) -> None:
    """Llamado desde POST /api/schedule después del commit."""
    try:
        await _get_redis().publish(CHANNEL_SCHEDULE, json.dumps(payload))
        logger.debug("realtime: published schedule_updated")
    except Exception as e:  # noqa: BLE001
        # Si Redis está caído no perdemos la actualización en sí — la app
        # igual va a hacer pickup en el próximo polling (cada 30s/15min).
        logger.warning("realtime publish failed (degradado a polling): %s", e)


async def subscribe_schedule() -> AsyncIterator[dict[str, Any]]:
    """Generador async que yielda mensajes del channel schedule.

    Cada SSE connection abre su propio iterador. La conexión Redis pubsub
    es cancel-safe: si el cliente se desconecta, el generator termina y la
    suscripción se cierra limpia.
    """
    redis = _get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL_SCHEDULE)
    try:
        async for raw in pubsub.listen():
            if raw.get("type") != "message":
                continue
            data = raw.get("data")
            if not data:
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                logger.warning("realtime: payload no parseable, ignorado")
    finally:
        await pubsub.unsubscribe(CHANNEL_SCHEDULE)
        await pubsub.aclose()
