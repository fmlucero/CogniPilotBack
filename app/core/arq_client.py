"""arq Redis pool singleton para enqueuing desde FastAPI.

Inicializado en el lifespan de main.py, cerrado al shutdown.
Usado por los endpoints que necesitan disparar tareas async (ej. schedule POST → FCM push).
"""
from __future__ import annotations

import logging

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_pool: ArqRedis | None = None


async def init_arq_pool() -> ArqRedis:
    """Inicializa el pool. Llamado desde el lifespan."""
    global _pool
    if _pool is not None:
        return _pool
    settings = get_settings()
    _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    logger.info("arq pool initialized against %s", settings.redis_url)
    return _pool


async def close_arq_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
        logger.info("arq pool closed")


def get_arq_pool() -> ArqRedis:
    """Acceso al pool inicializado. Lanza si todavía no se llamó init_arq_pool()."""
    if _pool is None:
        raise RuntimeError("arq pool not initialized (lifespan should call init_arq_pool first)")
    return _pool
