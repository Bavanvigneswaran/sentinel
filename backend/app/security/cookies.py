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


#: The agent-build download ticket. A separate cookie from the refresh token
#: because it authorises one thing only — fetching a published binary — and
#: because it is short-lived where that one is not.
#:
#: `Path="/"` for the same reason the refresh cookie uses it, and it is the
#: reason this is a cookie at all rather than a scoped one: the Vite dev proxy
#: and `ApiPrefixMiddleware` both rewrite `/api/*` to `/*`, so a cookie scoped
#: to `/downloads` would be stored under that path by the browser and never
#: sent back on a navigation to `/api/downloads/...`. Being ambient is
#: acceptable here in a way it would not be for a stronger credential: it is
#: HttpOnly, SameSite=strict, expires in ten minutes, and the most it can do is
#: download a binary the download page already offers.
DOWNLOAD_COOKIE_NAME = "sentinel_download"  # noqa: S105 — a cookie name


def set_download_cookie(response: Response, value: str, *, max_age: int) -> None:
    settings = get_settings()
    response.set_cookie(
        key=DOWNLOAD_COOKIE_NAME,
        value=value,
        max_age=max_age,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
    )
