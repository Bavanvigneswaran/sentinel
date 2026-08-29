"""Origin checking on both WebSocket handshakes.

Neither socket was exploitable without it: the viewer's ticket is minted under
a Bearer token the page holds in memory, and the ingest socket authenticates
with a header a browser cannot set on a handshake. But both of those are facts
about code in other files, and a cross-site handshake carrying a valid ticket
*was* accepted — confirmed against a running stack. The invariant belongs at
the handshake.
"""

import pytest

from app.security.origins import is_allowed_origin

HOST = "sentinel.example:8000"


def test_a_request_with_no_origin_is_allowed():
    """Every browser sends one on a WebSocket handshake; nothing else does.

    The Python agent, the Kotlin collector and React Native all connect without
    it, so refusing an absent Origin would refuse every real agent while
    stopping no attacker — a browser cannot suppress the header.
    """
    assert is_allowed_origin(None, HOST) is True
    assert is_allowed_origin("", HOST) is True


def test_same_origin_is_allowed():
    assert is_allowed_origin(f"https://{HOST}", HOST) is True
    # Compared on host and port, never scheme: behind the Funnel --proxy-headers
    # rewrites the scheme while Host stays the bare name.
    assert is_allowed_origin(f"http://{HOST}", HOST) is True


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example",
        "https://evil.example",
        "https://sentinel.example.evil.com",
        "https://sentinel.example:9000",  # right host, wrong port
        # A sandboxed iframe or a file:// page — never a context this product
        # is loaded in, and exactly what an attacker reaches for.
        "null",
        "not-an-origin",
    ],
)
def test_a_foreign_origin_is_refused(origin):
    assert is_allowed_origin(origin, HOST) is False


def test_a_configured_cors_origin_is_allowed(monkeypatch):
    """Under the Vite dev proxy the page is on :5173 while the socket lands on
    :8000, so same-origin alone would refuse every dev session."""
    import app.security.origins as origins
    from app.config import Settings

    configured = Settings(
        _env_file=None, cors_origins=["http://localhost:5173"], environment="test"
    )
    monkeypatch.setattr(origins, "get_settings", lambda: configured)

    assert is_allowed_origin("http://localhost:5173", "localhost:8000") is True
    assert is_allowed_origin("http://localhost:9999", "localhost:8000") is False


async def test_the_viewer_socket_refuses_a_foreign_origin(live_server, make_user):
    """End to end, with a real ticket — the case that was accepted before."""
    import websockets

    from app.live.tickets import mint_ticket

    user = await make_user()
    ticket = await mint_ticket(user.id)
    url = f"{live_server.replace('http://', 'ws://')}/ws/viewer?ticket={ticket}"

    with pytest.raises(Exception):  # noqa: B017, PT011 — any refusal will do
        async with websockets.connect(
            url, additional_headers={"Origin": "http://evil.example"}, open_timeout=10
        ):
            pass


async def test_the_viewer_socket_still_accepts_its_own_origin(live_server, make_user):
    import websockets

    from app.live.tickets import mint_ticket

    user = await make_user()
    ticket = await mint_ticket(user.id)
    host = live_server.removeprefix("http://")
    url = f"ws://{host}/ws/viewer?ticket={ticket}"

    async with websockets.connect(
        url, additional_headers={"Origin": f"http://{host}"}, open_timeout=10
    ) as socket:
        assert socket is not None
