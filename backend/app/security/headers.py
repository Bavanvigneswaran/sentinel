"""Response security headers.

Every header here is a browser-side defence the server has to opt into: absent,
the browser applies its permissive default and nothing about the response says
anything is wrong. That is why this is one middleware wrapping everything
rather than a decorator anyone can forget — it runs outermost (registered last
in `app/main.py`, since Starlette applies middleware in reverse registration
order), so the console's static files, the API's JSON, and the agent binary
downloads all get the same treatment and no route can opt out by accident.

Pure ASGI rather than `BaseHTTPMiddleware` for the same reason
`ApiPrefixMiddleware` is: it is cheaper, and it lets us look at `scope` for the
one decision below that depends on the request rather than the response.

The Content-Security-Policy is written against what the console actually loads,
which was checked rather than guessed:

* `script-src 'self'` — the Vite build emits exactly one `<script type="module"
  src="/assets/index-*.js">` and no inline script, so no hash or nonce
  machinery is needed. If a future Vite config turns on the modulepreload
  polyfill (which *is* inline) this breaks loudly, which is the correct
  failure: silently adding 'unsafe-inline' to fix it would retire the whole
  directive.
* `style-src` needs `'unsafe-inline'`. uPlot positions its cursor and legend by
  writing inline style attributes on every frame, and CSP makes no distinction
  between a style attribute and a `<style>` block. There is no version of this
  that keeps live charts and drops the keyword.
* `worker-src 'self'` — `lib/webPush.ts` registers `/sw.js`. Omitting it falls
  back to `script-src`, which happens to be identical, but only by coincidence.
* `img-src 'self' data:` — `data:` for the inline SVG data URIs lucide-react
  emits.
* `connect-src 'self'` covers both the `/api` fetches and the viewer
  WebSocket: `lib/liveSocket.ts` builds its URL from `window.location.host`, and
  CSP3 matches a same-origin `ws://`/`wss://` against `'self'`. Verified in a
  browser against a live socket, because the older CSP2 behaviour did not.

`frame-ancestors 'none'` is the real clickjacking defence; `X-Frame-Options`
below is the same statement for browsers that never implemented it.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Paths whose responses are exempt from CSP only. FastAPI's Swagger UI and
#: ReDoc load their bundles from cdn.jsdelivr.net and boot from an inline
#: script, so `script-src 'self'` renders both as a blank page. They are
#: unreachable in prod anyway (`app/main.py` sets docs_url=None there), so this
#: exemption cannot widen a production policy — but CLAUDE.md tells you to run
#: a dev server when you need to read the schema, and that has to keep working.
CSP_EXEMPT_PATHS = frozenset({"/docs", "/redoc", "/docs/oauth2-redirect"})

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "worker-src 'self'",
        "manifest-src 'self'",
    )
)

#: Features this product never uses. A feature that is *not* named here keeps
#: its default allowlist, which is why the client-hint features are absent:
#: naming `ch-ua-arch` would switch it off, and `lib/platform.ts` reads it via
#: `getHighEntropyValues(["architecture"])` to decide which Mac build to offer.
#: That call already treats a block as "unknown", so breaking it would not
#: raise anything — the download page would just quietly stop distinguishing
#: Apple Silicon from Intel.
PERMISSIONS_POLICY = ", ".join(
    (
        "accelerometer=()",
        "camera=()",
        "geolocation=()",
        "gyroscope=()",
        "magnetometer=()",
        "microphone=()",
        "payment=()",
        "usb=()",
    )
)

#: One year, the minimum any preload list accepts. Deliberately without
#: `includeSubDomains` or `preload`: the public front door is a Tailscale
#: Funnel hostname under a shared `ts.net` domain, and this deployment has no
#: standing to make assertions about names it does not control. It is also not
#: reversible in a hurry — a browser that has seen the header keeps refusing
#: plaintext for the full year regardless of what the server later says.
STRICT_TRANSPORT_SECURITY = "max-age=31536000"

#: Applied to every response. `nosniff` matters most on the endpoints that
#: serve an attacker-influenced filename (`/downloads/agent/{filename}`),
#: where a sniffed content type is the whole attack.
_ALWAYS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", PERMISSIONS_POLICY.encode("latin-1")),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
)


class SecurityHeadersMiddleware:
    """Add the standard browser security headers to every HTTP response."""

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        # A WebSocket handshake carries no response headers a browser reads for
        # any of this, and `websocket.accept` has no `headers` key to append to
        # in the first place.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # Read the scheme rather than the environment: behind the Tailscale
        # Funnel, uvicorn's --proxy-headers rewrites this to "https", and over
        # the LAN address it stays "http". So the header appears exactly where
        # it means something, and a browser on plaintext is never told to
        # refuse plaintext to a host that only speaks it.
        secure = scope.get("scheme") == "https"

        async def send_with_headers(message) -> None:  # noqa: ANN001
            if message["type"] == "http.response.start":
                message["headers"] = list(
                    _merge(message.get("headers") or [], path=path, secure=secure)
                )
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _merge(
    existing: Iterable[tuple[bytes, bytes]], *, path: str, secure: bool
) -> list[tuple[bytes, bytes]]:
    """Append our headers to a response's, without clobbering a route's own."""
    headers = list(existing)
    present = {name.lower() for name, _ in headers}

    additions = list(_ALWAYS)
    if path not in CSP_EXEMPT_PATHS:
        additions.append((b"content-security-policy", CONTENT_SECURITY_POLICY.encode("latin-1")))
    if secure:
        additions.append((b"strict-transport-security", STRICT_TRANSPORT_SECURITY.encode()))

    headers.extend((name, value) for name, value in additions if name not in present)
    return headers


__all__ = [
    "CONTENT_SECURITY_POLICY",
    "CSP_EXEMPT_PATHS",
    "PERMISSIONS_POLICY",
    "STRICT_TRANSPORT_SECURITY",
    "SecurityHeadersMiddleware",
]
