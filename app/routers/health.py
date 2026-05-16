"""Healthcheck endpoints — usados por Docker healthcheck y nginx upstream."""
from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import text

from app.core.deps import DBSession

router = APIRouter(tags=["health"])


@router.get("/health", status_code=status.HTTP_200_OK)
async def health() -> dict[str, str]:
    """Liveness — siempre 200 si el proceso responde."""
    return {"status": "ok"}


@router.get("/health/db", status_code=status.HTTP_200_OK)
async def health_db(db: DBSession) -> dict[str, str]:
    """Readiness — chequea conexión a Postgres."""
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}
