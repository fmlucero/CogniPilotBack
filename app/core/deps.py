"""FastAPI dependencies: DB session, current user, role guard."""
from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import AccessPayload, verify_access

# Cookie names — keep in sync with cognipilot-remote (lib/auth.ts)
ACCESS_COOKIE = "cp_at"
REFRESH_COOKIE = "cp_rt"


DBSession = Annotated[AsyncSession, Depends(get_session)]


def _extract_token(authorization: str | None, cp_at: str | None) -> str | None:
    """Extract access token from Authorization header (Bearer) or cookie."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return cp_at


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    cp_at: Annotated[str | None, Cookie()] = None,
) -> AccessPayload:
    """Resolve the current user from JWT.

    Looks at the Authorization header first (mobile app), then the cookie (web).
    Raises 401 if missing or invalid.
    """
    token = _extract_token(authorization, cp_at)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    payload = verify_access(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


CurrentUser = Annotated[AccessPayload, Depends(get_current_user)]


def require_roles(*roles: str):
    """Dependency factory that enforces one of the given roles.

    Usage:
        @router.get("/admin", dependencies=[Depends(require_roles("admin_sistema"))])
    """
    allowed = set(roles)

    async def _checker(user: CurrentUser) -> AccessPayload:
        if user["rol"] not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return user

    return _checker


# Optional user — for endpoints that work both authed and anonymous
async def get_optional_user(
    authorization: Annotated[str | None, Header()] = None,
    cp_at: Annotated[str | None, Cookie()] = None,
) -> AccessPayload | None:
    token = _extract_token(authorization, cp_at)
    if not token:
        return None
    return verify_access(token)


OptionalUser = Annotated[AccessPayload | None, Depends(get_optional_user)]
