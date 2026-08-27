"""Single-purpose tickets for authenticating an agent-build download.

A build binary can be tens of megabytes, and a mobile connection that stalls
partway through has to resume with an HTTP Range request against the same
URL — something only the browser's own download manager can do, and only for
a plain `<a download>` link. That link can't carry an `Authorization` header,
so it needs its own credential, minted over the authenticated REST API and
then handed to the browser in the query string.

Unlike `app/live/tickets.py`'s WebSocket ticket, this one is not single-use:
GETDEL would burn it on the download manager's first byte-range request,
breaking resume before it started. It stays valid for any number of requests
against its one bound filename until TICKET_TTL_SECONDS runs out.
"""

from __future__ import annotations

from app.redis import get_redis
from app.security.opaque import new_secret, sha256_bytes

#: Generous enough for a large binary to finish over a slow or interrupted
#: connection, including retries; still a short-lived credential rather than
#: a standing link.
TICKET_TTL_SECONDS = 600
_KEY_PREFIX = "dl:ticket:"


def _key(ticket: str) -> str:
    return _KEY_PREFIX + sha256_bytes(ticket).hex()


async def mint_download_ticket(filename: str) -> str:
    """Issue a fresh ticket bound to exactly this filename."""
    ticket = new_secret()
    await get_redis().set(_key(ticket), filename, ex=TICKET_TTL_SECONDS)
    return ticket


async def check_download_ticket(ticket: str, filename: str) -> bool:
    """True if `ticket` is unexpired and was minted for this exact filename.

    A plain GET, not GETDEL — see the module docstring on why this must
    survive being read more than once.
    """
    bound_filename = await get_redis().get(_key(ticket))
    return bound_filename is not None and bound_filename == filename
