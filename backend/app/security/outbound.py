"""Validation for URLs this server will make a request to.

The web-push endpoint is the only URL in the product that a *user* chooses and
the *server* then fetches, which makes it the one place server-side request
forgery can start. Everything else the server talks to — Postgres, Redis, SMTP,
FCM — comes from configuration an operator wrote.

The check runs twice, and the two halves are deliberately different:

* `validate_push_endpoint()` is syntactic and offline, so it can sit on the
  Pydantic schema and reject a bad endpoint at registration with a 422 that
  says why. It never resolves a name, because a schema validator that makes a
  DNS query is a schema validator that hangs.
* `resolve_is_public()` resolves and is called immediately before the request
  is issued. A name that passed the first check can still point at 127.0.0.1
  by the time it is used — either because the record changed or because it was
  rebound on purpose — and the address is what matters, not the string.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

#: Web push is defined over TLS. A `http://` endpoint is either a mistake or an
#: attempt to reach something local, and neither is worth accepting.
ALLOWED_SCHEMES = frozenset({"https"})

#: A single-label host cannot be a public push service, and is exactly what an
#: internal name looks like — `redis`, `localhost`, `metadata`, a container
#: name on a shared network.
_MIN_LABELS = 2


class UnsafeUrl(ValueError):
    """The URL is one this server must not make a request to."""


def _address_is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def validate_push_endpoint(url: str) -> str:
    """Return `url` unchanged, or raise `UnsafeUrl` naming what is wrong.

    Offline: an IP literal is checked directly, a hostname only for shape.
    `resolve_is_public()` is the half that looks at where a name actually goes.
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:  # a malformed IPv6 literal, mostly
        raise UnsafeUrl("endpoint is not a valid URL") from exc

    if parts.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrl("endpoint must be an https:// URL")

    try:
        host = parts.hostname
    except ValueError as exc:
        raise UnsafeUrl("endpoint has an invalid host") from exc
    if not host:
        raise UnsafeUrl("endpoint must have a host")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A name, not a literal. Only its shape is checkable without a lookup.
        if host.count(".") < _MIN_LABELS - 1 or host.endswith("."):
            raise UnsafeUrl(
                "endpoint host must be a fully qualified public hostname"
            ) from None
        return url

    if not _address_is_public(address):
        raise UnsafeUrl("endpoint must not address a private or loopback host")
    return url


def resolve_is_public(url: str) -> bool:
    """True when every address `url`'s host resolves to is publicly routable.

    Called just before the request goes out. A failure to resolve is False, not
    an exception: the caller's job is to skip the send, and a name that does not
    resolve is not one we were going to reach anyway.
    """
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    if not host:
        return False

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return _address_is_public(address)

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False

    # Every answer has to be public. One private record among several is enough
    # to reach an internal host, since which one is used is not ours to choose.
    resolved = {info[4][0] for info in infos}
    if not resolved:
        return False
    for raw in resolved:
        try:
            if not _address_is_public(ipaddress.ip_address(raw)):
                return False
        except ValueError:
            return False
    return True


__all__ = ["UnsafeUrl", "resolve_is_public", "validate_push_endpoint"]
