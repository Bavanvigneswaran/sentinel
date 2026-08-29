"""Every response carries the browser security headers.

These are the headers whose absence is invisible: nothing fails, no test goes
red, the browser just applies its permissive default. So the assertions here are
deliberately about *coverage* — that the middleware sits outside everything and
no response shape can slip past it — rather than about the exact policy string,
which lives in app/security/headers.py and is allowed to change.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.security.headers import (
    CONTENT_SECURITY_POLICY,
    CSP_EXEMPT_PATHS,
    SecurityHeadersMiddleware,
    _merge,
)

ALWAYS_PRESENT = (
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
    "cross-origin-resource-policy",
)


async def test_health_carries_every_header(client):
    response = await client.get("/health")
    assert response.status_code == 200
    for header in ALWAYS_PRESENT:
        assert header in response.headers, f"{header} missing"
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY


async def test_headers_reach_an_error_response(client):
    """A 401 is a response too, and the one an attacker sees most of."""
    response = await client.get("/auth/me")
    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in response.headers


async def test_headers_reach_a_404(client):
    response = await client.get("/no-such-route")
    assert response.status_code == 404
    assert response.headers["x-frame-options"] == "DENY"


async def test_headers_survive_gzip(client):
    """The middleware runs outside GZipMiddleware, so a compressed response
    must still carry them — this is the ordering in app/main.py, asserted."""
    response = await client.get(
        "/devices", headers={"Accept-Encoding": "gzip", "Authorization": "Bearer nope"}
    )
    assert "content-security-policy" in response.headers


async def test_hsts_only_over_https(client):
    """Present on TLS, absent on plaintext.

    The scheme is read from the ASGI scope rather than from settings, so behind
    the Funnel (where --proxy-headers rewrites it) the header appears, and over
    the LAN http:// address it does not. Telling a browser to refuse plaintext
    to a host that only speaks plaintext would take the deployment offline for
    a year, and no amount of later configuration takes it back.
    """
    secure = await client.get("/health")
    assert secure.headers["strict-transport-security"].startswith("max-age=")

    from app.main import create_app

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as plain:
        insecure = await plain.get("/health")
    assert "strict-transport-security" not in insecure.headers


@pytest.mark.parametrize("path", sorted(CSP_EXEMPT_PATHS))
async def test_docs_are_exempt_from_csp_only(client, path):
    """Swagger UI boots from an inline script and a CDN bundle, so a CSP would
    render it blank. The exemption is CSP-only, and cannot reach production:
    app/main.py sets docs_url=None when ENVIRONMENT=prod."""
    response = await client.get(path)
    if response.status_code == 404:
        pytest.skip(f"{path} is not mounted in this configuration")
    assert "content-security-policy" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


def test_merge_does_not_clobber_a_route_header():
    """A route that sets its own policy keeps it."""
    own = [(b"content-security-policy", b"default-src 'none'")]
    merged = dict(_merge(own, path="/anything", secure=True))
    assert merged[b"content-security-policy"] == b"default-src 'none'"
    assert merged[b"x-frame-options"] == b"DENY"


def test_merge_preserves_existing_headers():
    existing = [(b"content-type", b"application/json"), (b"content-length", b"2")]
    merged = _merge(existing, path="/health", secure=False)
    assert existing[0] in merged and existing[1] in merged


async def test_middleware_passes_websocket_scope_through():
    """A `websocket.accept` message has no `headers` key to append to, and a
    browser reads none of this from a handshake anyway."""
    sent = []

    async def app(scope, receive, send):
        await send({"type": "websocket.accept"})

    async def record(message):
        sent.append(message)

    await SecurityHeadersMiddleware(app)(
        {"type": "websocket", "path": "/ws/viewer"}, None, record
    )
    assert sent == [{"type": "websocket.accept"}]


async def test_the_csp_carries_no_unsafe_inline_anywhere(client):
    """The console needs no inline style, and the reason it was once thought to
    is worth a regression guard.

    The belief was that uPlot positions its cursor and legend by writing inline
    style *attributes* every frame. It does not: it assigns CSSOM properties
    (`el.style.transform = ...`), which CSP does not govern at all — the script
    doing the assigning already passed `script-src`. Only a `<style>` element, a
    `style="..."` attribute parsed from markup, or `setAttribute("style", ...)`
    is subject to `style-src`, and the built console produces none of the three.

    Verified in a browser against five live charts streaming from a real agent
    before this was tightened; this is the guard that stops the keyword coming
    back on the strength of the old explanation.
    """
    csp = (await client.get("/health")).headers["content-security-policy"]
    assert "'unsafe-inline'" not in csp
    assert "style-src 'self'" in csp
    assert "style-src-attr 'none'" in csp


async def test_script_src_is_still_free_of_the_keyword(client):
    """The half that always mattered. If a future Vite config turns on the
    inline modulepreload polyfill the console breaks loudly, which is the
    correct failure — it must not be fixed by widening the directive."""
    csp = (await client.get("/health")).headers["content-security-policy"]
    assert "script-src 'self'" in csp
