"""Password-reset tokens, and the email that carries one.

Modelled on app/live/tickets.py rather than on a new table: the secret is a
high-entropy random value, sha256'd at rest so a Redis dump is a list of
hashes rather than a list of live reset links, and it is consumed with GETDEL
so two requests racing the same link cannot both succeed. Storing it in Redis
also makes expiry the store's job rather than a sweep's, and is why this
feature needed no migration.

Two deliberate differences from the viewer ticket:

* The TTL is 30 minutes, not 30 seconds. This credential travels through a
  mail server and a person's attention span; a link that expires before it is
  read is a support request, not a security control.
* The stored value carries a fingerprint of the password hash the token was
  minted against, so completing a reset invalidates every *other* outstanding
  link for that account as a side effect. Without it, two links requested
  minutes apart would each work once, and a reset performed from the older one
  would silently undo the newer.

Losing Redis loses outstanding links. That is the correct trade here: the user
asks for another one, and nothing durable was riding on it.

The email is sent from this module rather than through app/alerts/notify.py's
`_send_email`, for the reason app/reports/mailer.py already gives for staying
separate: that function's caller is an alert dispatch loop whose
graceful-absence behaviour (log and continue, so one dead channel never stops
a sweep) is wrong here. A reset email that silently does not send is a user
locked out with no way to find out why, so this one reports failure to its
caller instead.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import quote

import aiosmtplib

from app.config import get_settings
from app.redis import get_redis
from app.security.opaque import new_secret, sha256_bytes

logger = logging.getLogger(__name__)

_KEY_PREFIX = "auth:pwreset:"
_SEPARATOR = ":"


class ResetEmailUnavailable(Exception):
    """No mail could be sent, so no reset link reached the user."""


def _key(token: str) -> str:
    return _KEY_PREFIX + sha256_bytes(token).hex()


def password_fingerprint(password_hash: str) -> str:
    """A short, non-reversible tag for the hash a token was minted against.

    The argon2 hash itself never goes into Redis: it is the verifier, and a
    reset store is not the place to keep a second copy of it.
    """
    return sha256_bytes(password_hash).hex()[:16]


@dataclass(frozen=True, slots=True)
class ResetClaim:
    user_id: uuid.UUID
    fingerprint: str


async def mint_reset_token(user_id: uuid.UUID, password_hash: str) -> str:
    """Issue a fresh token. Returned once; only its hash lives in Redis."""
    token = new_secret()
    await get_redis().set(
        _key(token),
        f"{user_id}{_SEPARATOR}{password_fingerprint(password_hash)}",
        ex=get_settings().password_reset_ttl_seconds,
    )
    return token


async def redeem_reset_token(token: str) -> ResetClaim | None:
    """Resolve and consume a token in one atomic step.

    GETDEL for the same reason app/live/tickets.py uses it: a link that is
    double-clicked, forwarded, or replayed must succeed at most once.
    """
    if not token:
        return None
    raw = await get_redis().getdel(_key(token))
    if raw is None:
        return None
    user_id, _, fingerprint = raw.partition(_SEPARATOR)
    try:
        return ResetClaim(uuid.UUID(user_id), fingerprint)
    except ValueError:
        return None


def build_reset_url(base_url: str, token: str) -> str:
    """The link that goes in the email.

    The token rides in a query string, which this codebase otherwise avoids
    for credentials (see app/security/cookies.py's download cookie). A link in
    an email has nowhere else to carry it, so the exposure is bounded from the
    other end instead: the token is single-use, expires in 30 minutes, is
    invalidated by the reset it authorises, and the reset page strips it from
    the address bar as soon as it has been read.
    """
    return f"{base_url.rstrip('/')}/reset-password?token={quote(token, safe='')}"


def _body(reset_url: str, ttl_seconds: int) -> str:
    minutes = max(1, ttl_seconds // 60)
    return (
        "Someone asked to reset the password on your Sentinel account.\n\n"
        f"Open this link to choose a new one:\n\n{reset_url}\n\n"
        f"The link works once and expires in {minutes} minutes.\n\n"
        "If this wasn't you, nothing has changed and you can ignore this "
        "email. Your password stays as it is until the link above is used.\n"
    )


async def send_reset_email(to_address: str, reset_url: str) -> None:
    """Deliver the reset link, or raise.

    Unlike every other mail path in this codebase, a failure here is raised
    rather than swallowed: the alert and report senders are best-effort
    because something else will try again on the next sweep, and nothing will
    try again for this one.
    """
    settings = get_settings()
    if not settings.smtp_host:
        raise ResetEmailUnavailable("smtp_host is not configured")

    message = EmailMessage()
    message["From"] = settings.smtp_from or settings.smtp_username or "sentinel@localhost"
    message["To"] = to_address
    message["Subject"] = "Reset your Sentinel password"
    message.set_content(_body(reset_url, settings.password_reset_ttl_seconds))

    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            start_tls=settings.smtp_use_tls,
        )
    except Exception as exc:  # noqa: BLE001 — re-raised as our own type below
        # Logged without the address's token: the URL is a live credential
        # until it is spent, and an exception traceback is not the place for it.
        logger.warning("password reset email failed to=%s", to_address, exc_info=True)
        raise ResetEmailUnavailable(str(exc)) from exc
