"""Single-use tickets for authenticating the viewer WebSocket.

A browser cannot set an `Authorization` header on a WebSocket handshake, and
putting the 15-minute access JWT in the query string would land it in proxy
logs, access logs, and browser history — the same reasoning that keeps the
access token out of localStorage in the first place. A ticket is a
purpose-built, 30-second, single-use credential instead: minted over the
authenticated REST API, then spent immediately on the socket connect.

Mirrors the agent-token pattern in app/security/opaque.py: high-entropy random
value, sha256'd at rest, no constant-time comparison needed because lookup is
by unique key, not by compare.
"""

from __future__ import annotations

import uuid

from app.redis import get_redis
from app.security.opaque import new_secret, sha256_bytes

TICKET_TTL_SECONDS = 30
_KEY_PREFIX = "ws:ticket:"


def _key(ticket: str) -> str:
    return _KEY_PREFIX + sha256_bytes(ticket).hex()


async def mint_ticket(user_id: uuid.UUID) -> str:
    """Issue a fresh ticket bound to this user. Returned once, never stored in
    plaintext — only its hash lives in Redis."""
    ticket = new_secret()
    await get_redis().set(_key(ticket), str(user_id), ex=TICKET_TTL_SECONDS)
    return ticket


async def redeem_ticket(ticket: str) -> uuid.UUID | None:
    """Resolve and consume a ticket in one atomic step.

    GETDEL rather than GET-then-DEL: two viewer sockets racing on the same
    (stolen, replayed, or double-clicked) ticket must not both succeed.
    """
    raw = await get_redis().getdel(_key(ticket))
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None
