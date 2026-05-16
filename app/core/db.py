"""SQLAlchemy 2.0 async engine + session factory.

The engine is shared across the whole process. Each request gets its own
AsyncSession via the FastAPI dependency `get_db` in `app.core.deps`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Base class for all ORM models."""


_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_pre_ping=True,
    # PgBouncer transaction pooling does not support server-side prepared
    # statements; disable them in asyncpg to avoid stale-prepared errors.
    connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
    echo=_settings.is_dev,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an AsyncSession per request."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
