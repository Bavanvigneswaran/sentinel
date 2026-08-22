"""The refresh-token cookie.

Path is always "/". The Vite dev proxy rewrites /api/* to /*, so a cookie scoped
to /auth would be stored under /auth by the browser but never sent back on a
request to /api/auth/refresh — refresh would silently 401 forever.
"""

from __future__ import annotations

from fastapi import Response

from app.config import get_settings

COOKIE_PATH = "/"


def set_refresh_cookie(response: Response, value: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=value,
        max_age=settings.jwt_refresh_ttl_seconds,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
    )


def clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    # The flags must match the ones used to set it, or the browser treats this
    # as a different cookie and the clear silently does nothing.
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
    )
