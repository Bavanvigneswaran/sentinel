"""Serving the built web console from the API process.

The failure this guards against is subtle and expensive: a catch-all that
returns index.html for *everything* makes a typo'd API endpoint answer 200 with
HTML, so a broken request looks like a frontend routing bug rather than a 404.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.webapp import ApiPrefixMiddleware, resolve_dist

SIGNUP = "/auth/signup"
CREDS = {"email": "console-owner@example.com", "password": "a-perfectly-fine-password"}


def _build_dist(tmp_path):
    """A minimal stand-in for `npm run build`'s output."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Sentinel</title>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    return dist


# --- finding a build --------------------------------------------------------


def test_a_built_console_is_found(tmp_path):
    assert resolve_dist(str(_build_dist(tmp_path))) is not None


def test_an_unbuilt_console_is_simply_absent(tmp_path):
    """Not an error. The API serves its own routes exactly as before — same
    posture as unset SMTP/VAPID/AGENT_DIST_DIR."""
    assert resolve_dist(str(tmp_path / "nope")) is None


def test_a_directory_without_an_index_does_not_count(tmp_path):
    """A half-deleted or half-copied dist would otherwise 500 on every page."""
    empty = tmp_path / "dist"
    empty.mkdir()
    assert resolve_dist(str(empty)) is None


# --- the /api prefix --------------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        ("/api/auth/login", "/auth/login"),
        ("/api/devices", "/devices"),
        ("/api", "/"),
        ("/api/", "/"),
        # Not a prefix match — these must be left completely alone.
        ("/apiary", "/apiary"),
        ("/devices", "/devices"),
        ("/", "/"),
    ],
)
async def test_the_api_prefix_is_stripped_exactly_like_the_proxy(sent, expected):
    seen = {}

    async def app(scope, receive, send):
        seen["path"] = scope["path"]

    await ApiPrefixMiddleware(app)({"type": "http", "path": sent}, None, None)
    assert seen["path"] == expected


async def test_the_prefix_is_stripped_for_websockets_too():
    """A Starlette HTTP middleware never sees a WS handshake, which is why this
    is pure ASGI — /api/ws/tickets is a normal request but the socket that
    follows is not."""
    seen = {}

    async def app(scope, receive, send):
        seen["path"] = scope["path"]

    await ApiPrefixMiddleware(app)({"type": "websocket", "path": "/api/ws/viewer"}, None, None)
    assert seen["path"] == "/ws/viewer"


async def test_the_middleware_does_not_mutate_the_callers_scope():
    original = {"type": "http", "path": "/api/devices"}

    async def app(scope, receive, send):
        pass

    await ApiPrefixMiddleware(app)(original, None, None)
    assert original["path"] == "/api/devices"


async def test_the_api_prefix_works_end_to_end(client):
    """The console calls /api/auth/signup; FastAPI serves /auth/signup."""
    resp = await client.post("/api" + SIGNUP, json=CREDS)
    assert resp.status_code == 201, resp.text


# --- what the catch-all must NOT swallow ------------------------------------


# --- serving it -------------------------------------------------------------

#: What a browser sends on a top-level navigation, and no fetch/XHR ever does.
NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html,application/xhtml+xml"}
#: What the console's own API calls look like.
XHR = {"Sec-Fetch-Mode": "cors", "Accept": "application/json"}


@pytest.fixture
async def console_client(tmp_path, monkeypatch):
    """A client for an app that IS serving a (stand-in) built console."""
    from httpx import ASGITransport, AsyncClient

    import app.main

    dist = _build_dist(tmp_path)
    base = Settings(_env_file=None, environment="test")
    monkeypatch.setattr(
        app.main,
        "get_settings",
        lambda: base.model_copy(update={"serve_web_console": True, "web_dist_dir": str(dist)}),
    )
    built = app.main.create_app()
    async with AsyncClient(transport=ASGITransport(app=built), base_url="https://test") as ac:
        yield ac


async def test_the_root_serves_the_console(console_client):
    resp = await console_client.get("/", headers=NAV)
    assert resp.status_code == 200
    assert "<!doctype html>" in resp.text.lower()


@pytest.mark.parametrize(
    "route", ["/devices", "/alerts/rules", "/incidents/abc", "/download", "/settings"]
)
async def test_client_side_routes_serve_the_console(console_client, route):
    """A hard refresh on /alerts/rules must not 404 — the router lives in the
    browser and the server has never heard of that path.

    /devices is the interesting one: it is *also* a real REST endpoint, so a
    catch-all route could never have served it (the API route wins the match)
    and blocking API prefixes from a catch-all would have 404ed it.
    """
    resp = await console_client.get(route, headers=NAV)
    assert resp.status_code == 200
    assert "<title>Sentinel</title>" in resp.text


async def test_the_same_path_is_still_the_api_for_a_non_browser_client(console_client):
    """The Android app calls GET /devices with no Sec-Fetch-Mode. It must get
    JSON — a 401 here, since it sent no token — never the console's HTML."""
    resp = await console_client.get("/devices")
    assert resp.status_code == 401
    assert "<!doctype html>" not in resp.text.lower()


async def test_the_consoles_own_api_calls_are_never_intercepted(console_client):
    """A fetch() from the loaded page sends Sec-Fetch-Mode: cors."""
    resp = await console_client.get("/devices", headers=XHR)
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/json")


async def test_an_api_call_under_the_api_prefix_is_never_a_navigation(console_client):
    """Belt and braces: /api/* is unambiguously the console talking to its
    backend, whatever headers a client sets."""
    resp = await console_client.get("/api/devices", headers=NAV)
    assert resp.status_code == 401


async def test_hashed_assets_are_served_and_cached_forever(console_client):
    resp = await console_client.get("/assets/index-abc123.js", headers=NAV)
    assert resp.status_code == 200
    assert "immutable" in resp.headers["cache-control"]


#: What a browser sends for the bundle's own <script>/<link> — NOT "navigate".
SUBRESOURCE = {"Sec-Fetch-Mode": "no-cors", "Accept": "*/*"}


async def test_the_bundles_own_script_and_stylesheet_load(console_client):
    """The regression that rendered a blank page with two 404s.

    Gating file serving on `Sec-Fetch-Mode: navigate` looked right and passed
    every unit test, but a subresource sends `no-cors`, so the console's own JS
    and CSS 404ed and nothing rendered at all. Every real file is served
    whatever asked for it; nothing under dist/ can collide with an API route.
    """
    resp = await console_client.get("/assets/index-abc123.js", headers=SUBRESOURCE)
    assert resp.status_code == 200
    assert resp.text == "console.log(1)"


async def test_a_missing_subresource_is_not_answered_with_html(console_client):
    """A 404 for a genuinely absent file, not index.html with a 200 — which
    would make a stale asset reference look like a working page."""
    resp = await console_client.get("/assets/deleted-by-a-deploy.js", headers=SUBRESOURCE)
    assert resp.status_code == 404
    assert "<!doctype html>" not in resp.text.lower()


async def test_the_index_is_never_cached(console_client):
    """Cache index.html and a deploy keeps serving the previous bundle's
    <script> tag, forever, to anyone who visited before it."""
    resp = await console_client.get("/", headers=NAV)
    assert "no-cache" in resp.headers["cache-control"]


async def test_real_api_routes_still_win_for_api_clients(console_client):
    assert (await console_client.get("/health")).json() == {"status": "ok"}


async def test_the_agent_enrollment_path_is_untouched(console_client):
    """The desktop agent POSTs here with no browser headers at all; it must
    never meet the console."""
    resp = await console_client.post("/enroll", json={"code": "X", "device_name": "d"})
    assert resp.status_code in (400, 422)
    assert "<!doctype html>" not in resp.text.lower()


@pytest.mark.parametrize(
    "attempt",
    ["../../../etc/passwd", "..%2f..%2f..%2fetc%2fpasswd", "assets/../../../etc/passwd"],
)
async def test_path_traversal_out_of_the_dist_directory_is_refused(console_client, attempt):
    resp = await console_client.get(f"/{attempt}", headers=NAV)
    # Either the SPA index or a 404 — never a file from outside dist.
    assert "root:" not in resp.text


async def test_an_unbuilt_console_leaves_the_api_exactly_as_it_was(tmp_path, monkeypatch):
    """No build present is a supported state, not an error — the API serves
    only its own routes, same posture as unset SMTP/VAPID/AGENT_DIST_DIR."""
    from httpx import ASGITransport, AsyncClient

    import app.main

    base = Settings(_env_file=None, environment="test")
    monkeypatch.setattr(
        app.main,
        "get_settings",
        lambda: base.model_copy(update={"web_dist_dir": str(tmp_path / "never-built")}),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app.main.create_app()), base_url="https://test"
    ) as ac:
        resp = await ac.get("/", headers=NAV)

    assert resp.status_code == 404
    assert "<!doctype html>" not in resp.text.lower()


async def test_disabling_it_explicitly_also_works(tmp_path, monkeypatch):
    """SERVE_WEB_CONSOLE=false, for a deployment putting a real CDN or reverse
    proxy in front of the static files."""
    from httpx import ASGITransport, AsyncClient

    import app.main

    dist = _build_dist(tmp_path)
    base = Settings(_env_file=None, environment="test")
    monkeypatch.setattr(
        app.main,
        "get_settings",
        lambda: base.model_copy(
            update={"serve_web_console": False, "web_dist_dir": str(dist)}
        ),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app.main.create_app()), base_url="https://test"
    ) as ac:
        assert (await ac.get("/", headers=NAV)).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "index%00.html",  # a null byte makes Path.resolve() raise, not return
        "a" * 5000,  # longer than any filesystem will accept
    ],
)
async def test_a_path_the_filesystem_refuses_is_not_a_500(console_client, path):
    """`resolve()` raises ValueError on an embedded null byte and OSError on an
    over-long name. Both reached the client as a 500 before they were caught —
    found by an existing download-API traversal test, once a built console made
    this middleware active in the default fixture.
    """
    resp = await console_client.get(f"/{path}", headers=SUBRESOURCE)
    assert resp.status_code == 404
