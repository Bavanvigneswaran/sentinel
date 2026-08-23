"""Serving the built web console from the API process.

Until Phase 11 the web console only ever ran under `vite dev`, which binds to
localhost. That is correct for development and useless for the thing Phase 11
is actually about: handing somebody a build and having it work. A second
machine on the same network could not load the console at all, and the Android
app could not be pointed at "the same place the browser goes" because there was
no such place — the browser went to :5173 and the API lived on :8000.

So the API process serves the console too, and one origin
(`http://<host>:8000`) is the whole product: console, REST, and the viewer
WebSocket. That removes CORS from the picture entirely for a real deployment,
and it is what makes `EXPO_PUBLIC_API_URL` and the browser's address bar the
same string.

Unset degrades visibly rather than breaking: with no `web/dist` built, the API
serves exactly what it always did and says so once at startup, the same posture
as unset SMTP/VAPID/FCM/AGENT_DIST_DIR.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent

#: Where `npm run build` puts it. Resolved from this file, not the CWD, for the
#: same reason app/config.py resolves its env files that way.
DEFAULT_WEB_DIST = _REPO_ROOT / "web" / "dist"

INDEX = "index.html"


class ApiPrefixMiddleware:
    """Strip a leading `/api` from the request path.

    Phase 1's invariant is that FastAPI mounts `/auth`, `/devices` and the rest
    at the root, and that "production's reverse proxy must strip `/api` the
    same way the Vite dev proxy does". When the API process is itself the thing
    serving the console there is no reverse proxy to do it, so this is that
    rewrite — deliberately the same behaviour rather than a second convention.

    Pure ASGI rather than a `BaseHTTPMiddleware` subclass because it must also
    cover the `websocket` scope: `/api/ws/tickets` is a normal request, but a
    Starlette HTTP middleware never sees a WebSocket handshake at all.
    """

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == "/api" or path.startswith("/api/"):
                # Copy rather than mutate: the caller's scope dict is not ours.
                scope = dict(scope)
                scope["path"] = path[len("/api") :] or "/"
                raw = scope.get("raw_path")
                if isinstance(raw, bytes) and (raw == b"/api" or raw.startswith(b"/api/")):
                    scope["raw_path"] = raw[len(b"/api") :] or b"/"
        await self.app(scope, receive, send)


def resolve_dist(configured: str | None) -> Path | None:
    """The directory holding the built console, or None if there is not one."""
    candidate = Path(configured).expanduser() if configured else DEFAULT_WEB_DIST
    return candidate if (candidate / INDEX).is_file() else None


class WebConsoleMiddleware:
    """Serve the console for browser navigations, and nothing else.

    The obvious implementation — a catch-all route that returns index.html for
    anything unmatched — cannot work here, and finding out why is the whole
    reason this is a middleware. `/devices` is simultaneously a REST endpoint
    (the Android app calls it, per Phase 10a's "no /api prefix on a device")
    *and* a page in the console's client-side router. A catch-all never sees it
    at all: the real route matches first, so a browser typing that URL gets the
    API's 401 JSON instead of the app. Blocking every API prefix from the
    catch-all instead — which is what this originally did — breaks the mirror
    image, 404ing a hard refresh on /devices. The test suite caught both.

    So the split is by *what the client asked for*, not by path.
    `Sec-Fetch-Mode: navigate` is sent by every current browser on a top-level
    navigation and never by `fetch`/`XHR` (those send `cors`, `same-origin` or
    `no-cors`), which distinguishes "someone typed this URL" from "the app is
    calling its API" exactly, with no route table to keep in sync. The `Accept`
    fallback covers a browser too old to send it.

    A request under `/api/...` is never treated as a navigation: that prefix is
    unambiguously the console talking to its own backend.
    """

    def __init__(self, app, dist: Path) -> None:  # noqa: ANN001
        self.app = app
        self.dist = dist
        self.dist_root = dist.resolve()
        self.index = dist / INDEX

    @staticmethod
    def _serveable(scope) -> bool:  # noqa: ANN001
        """A GET/HEAD that this middleware is allowed to answer at all."""
        return (
            scope["type"] == "http"
            and scope.get("method") in ("GET", "HEAD")
            # `/api/*` is unambiguously the console talking to its own backend.
            and not scope.get("path", "").startswith("/api")
        )

    @staticmethod
    def _is_navigation(scope) -> bool:  # noqa: ANN001
        """Did a person type/click this URL, rather than code calling an API?

        Only consulted for paths that are NOT real files. A subresource — the
        bundle's <script> and <link> — sends `Sec-Fetch-Mode: no-cors`, not
        `navigate`, so gating file serving on this too returned 404 for the
        console's own JS and CSS and rendered a blank page. Found by loading
        it in a browser; every unit test passed.
        """
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        mode = headers.get(b"sec-fetch-mode", b"").decode("latin-1")
        if mode:
            return mode == "navigate"
        # Pre-Sec-Fetch-Metadata browsers. An API client sends `*/*` or
        # `application/json`; only a document request asks for HTML first.
        accept = headers.get(b"accept", b"").decode("latin-1")
        return "text/html" in accept

    def _static_file(self, path: str) -> Path | None:
        """A real file inside dist, or None.

        `path` is user input, so the resolved result is checked for containment
        before it is ever opened — the same allow-list-then-verify shape
        app/services/download_service.py uses for build artifacts.
        """
        if not path:
            return None
        try:
            candidate = (self.dist / path).resolve()
            candidate.relative_to(self.dist_root)
            return candidate if candidate.is_file() else None
        except (ValueError, OSError):
            # ValueError covers two distinct cases that both mean "not ours":
            # relative_to() failing (the path escaped dist) and resolve()
            # refusing a path with an embedded null byte — `%00` in a URL,
            # which decodes into the scope path and made this crash with a 500
            # instead of falling through. OSError catches a name too long for
            # the filesystem. None of them are a file we should serve.
            return None

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if not self._serveable(scope):
            await self.app(scope, receive, send)
            return

        relative = scope.get("path", "/").lstrip("/")
        asset = self._static_file(relative)

        # A real file is served whatever asked for it: the bundle's own script
        # and stylesheet are subresources, not navigations, and nothing under
        # dist/ can collide with an API route anyway.
        if asset is None and not self._is_navigation(scope):
            await self.app(scope, receive, send)
            return

        if asset is not None:
            # Vite content-hashes everything under assets/, so those are
            # immutable. index.html must never be cached, or a deploy keeps
            # serving the previous bundle's <script> tag to anyone who visited
            # before it.
            cache = (
                "public, max-age=31536000, immutable"
                if relative.startswith("assets/")
                else "no-cache"
            )
            response = FileResponse(asset, headers={"Cache-Control": cache})
        else:
            # Any other navigation is a client-side route (/devices,
            # /alerts/rules, /incidents/<id>) that only the browser knows about.
            response = FileResponse(self.index, headers={"Cache-Control": "no-cache"})

        await response(scope, receive, send)
