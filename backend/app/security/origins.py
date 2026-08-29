"""Origin checking for the two WebSocket handshakes.

Neither socket was exploitable without this — the viewer needs a ticket minted
under a Bearer token the page holds in memory and cannot be read cross-origin,
and the ingest socket authenticates with an `Authorization` header a browser
cannot set on a handshake at all. But both of those are properties of code
somewhere else. A change that let a ticket be minted from a cookie-authenticated
request would open cross-site WebSocket hijacking here with nothing in this file
failing, which is the wrong place for an invariant to live.

Two rules, and the first one is the one that matters:

* **A request with no `Origin` is allowed.** Every browser sends one on a
  WebSocket handshake; nothing else does. The Python agent, the Kotlin
  collector and React Native all connect without it, so refusing an absent
  Origin would refuse every real agent while stopping no attacker — a browser
  cannot suppress the header.
* **A request with an `Origin` must be same-origin, or configured.** Compared
  on host and port only, never scheme: behind the Funnel `--proxy-headers`
  rewrites the scheme to https while `Host` stays the bare name, and under the
  Vite dev proxy the page is on :5173 while the socket lands on :8000 — which
  is what `cors_origins` covers.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.config import get_settings


def _netloc(url: str) -> str | None:
    parts = urlsplit(url)
    return parts.netloc or None


def is_allowed_origin(origin: str | None, host: str | None) -> bool:
    """True when a handshake carrying `origin` may proceed against `host`."""
    if not origin:
        # A non-browser client. See the module docstring.
        return True

    # "null" is what a sandboxed iframe or a `file://` page sends. Neither is a
    # context this product is ever loaded in, and both are exactly what an
    # attacker reaches for.
    if origin == "null":
        return False

    origin_netloc = _netloc(origin)
    if origin_netloc is None:
        return False

    if host and origin_netloc == host:
        return True

    return any(
        _netloc(configured) == origin_netloc for configured in get_settings().cors_origins
    )


def websocket_origin_allowed(websocket) -> bool:  # noqa: ANN001 — starlette WebSocket
    """`is_allowed_origin` against a Starlette WebSocket's own headers."""
    return is_allowed_origin(
        websocket.headers.get("origin"), websocket.headers.get("host")
    )


__all__ = ["is_allowed_origin", "websocket_origin_allowed"]
