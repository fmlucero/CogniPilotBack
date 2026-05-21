"""JWT signing/verification + password hashing.

Bit-compatible with the cognipilot-remote (Next.js) backend:
  - Same algorithm (HS256)
  - Same claims: sub, email, rol, empresaId, iat, exp
  - Same secrets via env (JWT_SECRET, JWT_REFRESH_SECRET)
  - Refresh tokens carry an extra claim: type="refresh"
  - bcrypt rounds=10 — existing hashes in DB validate as-is.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, TypedDict

import bcrypt
from jose import JWTError, jwt

from app.core.config import get_settings


class AccessPayload(TypedDict, total=False):
    """Access token claims (Next.js compatible).

    `impersonated_by` aparece solo en sesiones de impersonación (HU-34) y
    contiene el `usuario.id` del admin original. Se preserva en refresh para
    que la sesión sobreviva al ciclo de refresh transparente.
    """
    sub: str            # usuario.id (UUID) — el USER VISIBLE (target en impersonación)
    email: str
    rol: str            # "admin_sistema" | "supervisor" | "gerente" | "repartidor"
    empresaId: str | None
    iat: int
    exp: int
    impersonated_by: str | None  # HU-34: id del admin original; ausente o None si no es impersonación


class RefreshPayload(TypedDict, total=False):
    sub: str
    type: Literal["refresh"]
    iat: int
    exp: int
    impersonated_by: str | None  # HU-34


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sign_access(
    sub: str,
    email: str,
    rol: str,
    empresa_id: str | None,
    *,
    impersonated_by: str | None = None,
) -> str:
    settings = get_settings()
    now = _now()
    payload: dict = {
        "sub": sub,
        "email": email,
        "rol": rol,
        "empresaId": empresa_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_ttl_min)).timestamp()),
    }
    if impersonated_by:
        payload["impersonated_by"] = impersonated_by
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def sign_refresh(sub: str, *, impersonated_by: str | None = None) -> str:
    settings = get_settings()
    now = _now()
    payload: dict = {
        "sub": sub,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.refresh_token_ttl_days)).timestamp()),
    }
    if impersonated_by:
        payload["impersonated_by"] = impersonated_by
    return jwt.encode(payload, settings.jwt_refresh_secret, algorithm=settings.jwt_algorithm)


def verify_access(token: str) -> AccessPayload | None:
    settings = get_settings()
    try:
        decoded = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return decoded  # type: ignore[return-value]
    except JWTError:
        return None


def verify_refresh(token: str) -> RefreshPayload | None:
    settings = get_settings()
    try:
        decoded = jwt.decode(
            token, settings.jwt_refresh_secret, algorithms=[settings.jwt_algorithm]
        )
        if decoded.get("type") != "refresh":
            return None
        return decoded  # type: ignore[return-value]
    except JWTError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Password hashing — bcrypt, rounds=10 (matches bcryptjs default in Next back).
# ─────────────────────────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    salt = bcrypt.gensalt(rounds=10)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
