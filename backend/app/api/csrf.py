"""Cross-site request forgery defence for the cookie-authenticated routes.

`/auth/refresh` and `/auth/logout` authenticate with the `sentinel_refresh`
cookie alone. `SameSite=strict` is the primary defence and a good one — the
prod validator refuses to start with `cookie_samesite='none'`, so it cannot be
relaxed by configuration — but it was the *only* one, which made a single
setting the whole control.

This is the second layer, and it deliberately does not use a token.

A double-submit token would have to be readable by the client that echoes it,
which works in a browser and does not on Android: React Native's cookie jar is
the platform's (OkHttp via `android.webkit.CookieManager`), and JavaScript
there cannot read it without a native module, a prebuild and a signed APK
rebuild. A token scheme would therefore have shipped a change to the mobile
client that could not be verified without an emulator.

`Sec-Fetch-Site` needs no client change at all. Every current browser sets it
on every request, and — critically — page JavaScript cannot: it is a forbidden
header name, so an attacker's page cannot forge `same-origin` on a cross-site
request. Non-browser clients send nothing and are unaffected, which is what
keeps the Python agent, the Kotlin collector and React Native working
untouched.

The absent case is allowed on purpose. Refusing it would break every non-browser
client to guard against a browser too old to send the header (pre-16.4 Safari) —
and that browser is still covered by `SameSite=strict`. Layered, not doubled.

This is the same reasoning `WebConsoleMiddleware` already relies on with
`Sec-Fetch-Mode`, and the same trade-off: a header the browser controls is
better evidence than anything the page can claim about itself.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

#: `same-origin` is the console calling its own API. `none` is a user typing the
#: URL or following a bookmark — a top-level navigation, which cannot be a
#: forged POST. `same-site` is deliberately absent: this product is served from
#: one origin, so a sibling subdomain making authenticated calls is not a shape
#: that should exist.
ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})

CSRF_ERROR = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Cross-site request refused",
)


def enforce_same_site(request: Request) -> None:
    """Refuse a browser request that announces itself as cross-site.

    A FastAPI dependency. Raises 403 rather than 401: the credential may be
    perfectly valid, and telling the caller it was rejected for *where it came
    from* is more useful than implying the session is dead — which would send a
    legitimate client into a re-login loop.
    """
    site = request.headers.get("sec-fetch-site")
    if site is None:
        # Not a browser, or a browser too old to say. SameSite=strict still
        # applies; see the module docstring.
        return
    if site not in ALLOWED_FETCH_SITES:
        raise CSRF_ERROR


__all__ = ["ALLOWED_FETCH_SITES", "CSRF_ERROR", "enforce_same_site"]
