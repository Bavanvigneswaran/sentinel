"""Redis fixed-window rate limiting for the auth endpoints.

Hand-rolled rather than slowapi/fastapi-limiter: redis-py is already a
dependency, those libraries pin fastapi/starlette ranges that conflict with the
versions here, and auth rate limiting is security-critical code that should fit
on one screen and be directly testable.

A fixed window's worst case is 2x the limit across a boundary. At these numbers
that is irrelevant, and a sliding window is not worth the complexity.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Literal

from fastapi import HTTPException, Request, status

from app.config import get_settings
from app.redis import get_redis

logger = logging.getLogger(__name__)

KeyKind = Literal["ip", "email", "user"]


def _client_ip(request: Request) -> str:
    # Only request.client is trusted. X-Forwarded-For is attacker-controlled
    # unless a known proxy sets it; in production run uvicorn with
    # --proxy-headers --forwarded-allow-ips=<lb> so Starlette populates
    # request.client correctly instead of trusting the header here.
    return request.client.host if request.client else "unknown"


def _hash(value: str) -> str:
    # Keeps plaintext email addresses out of Redis.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


#: The per-process fallback used while Redis is unreachable. Keys already carry
#: their window bucket, so an entry is only ever valid for one window and old
#: ones are dropped on the next miss rather than expired on a timer.
_local_counts: dict[str, int] = {}
_LOCAL_MAX_KEYS = 10_000


def _local_hit(key: str, limit: int, window: int) -> tuple[bool, int]:
    """Count in this process, for when Redis cannot be reached.

    Weaker than the real limiter in two ways worth being explicit about: it is
    per-worker rather than per-deployment, and it forgets everything on a
    restart. It is still enormously better than the previous behaviour, which
    was no limit at all — brute-force protection on /auth/login and /enroll
    disappearing silently for the length of an outage, with one log line as the
    only sign. Failing *open* stays the right call; failing open all the way
    down to unlimited was not.
    """
    if len(_local_counts) > _LOCAL_MAX_KEYS:
        # Every key embeds its bucket, so anything not in the current window is
        # dead. Cheaper and more predictable than tracking expiry per key.
        current = str(int(time.time()) // window)
        for stale in [k for k in _local_counts if not k.endswith(f":{current}")]:
            del _local_counts[stale]

    count = _local_counts.get(key, 0) + 1
    _local_counts[key] = count
    return count <= limit, window


def reset_local_limiter() -> None:
    """Drop the fallback's state. For tests; nothing in the app calls it."""
    _local_counts.clear()


async def _hit(key: str, limit: int, window: int) -> tuple[bool, int]:
    """Increment the counter for this window. Returns (allowed, retry_after)."""
    redis = get_redis()
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        # NX so a burst cannot keep pushing the expiry forward and make the
        # window last indefinitely. Requires Redis >= 7.0.
        pipe.expire(key, window, nx=True)
        pipe.ttl(key)
        count, _, ttl = await pipe.execute()
    return count <= limit, max(int(ttl), 1)


class RateLimit:
    """A FastAPI dependency enforcing one limit on one dimension."""

    def __init__(self, scope: str, limit: int, window: int, key: KeyKind = "ip") -> None:
        self.scope = scope
        self.limit = limit
        self.window = window
        self.key = key

    async def _identity(self, request: Request) -> str | None:
        if self.key == "ip":
            return _client_ip(request)
        if self.key == "email":
            # Reading the body here is safe: Starlette caches it, so the route
            # handler still receives it.
            try:
                body = await request.json()
            except Exception:
                return None
            email = body.get("email") if isinstance(body, dict) else None
            return _hash(email.strip().lower()) if isinstance(email, str) else None
        return None

    async def __call__(self, request: Request) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return

        identity = await self._identity(request)
        if identity is None:
            return

        bucket = int(time.time()) // self.window
        key = f"rl:{self.scope}:{self.key}:{identity}:{bucket}"

        try:
            allowed, retry_after = await _hit(key, self.limit, self.window)
        except Exception:
            # Degrade, rather than disappear. A Redis outage must not lock every
            # user out of the product — that part of the original trade-off is
            # unchanged and correct — but it must not silently remove the limit
            # from /auth/login and /enroll either. The in-process counter is
            # per-worker and does not survive a restart, so it is a weaker
            # control, not an equivalent one. Production must still alert on
            # this log line: it is the only sign the strong limiter is gone.
            logger.error(
                "rate limiter unavailable, degrading to the in-process fallback",
                exc_info=True,
            )
            allowed, retry_after = _local_hit(key, self.limit, self.window)

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )


def _s():
    return get_settings()


# Instantiated lazily inside the route module so settings are read at import of
# the app, not at import of this module.
def login_ip_limit() -> RateLimit:
    return RateLimit("login", _s().rl_login_per_minute, 60, key="ip")


def login_email_limit() -> RateLimit:
    return RateLimit("login_email", _s().rl_login_per_email_per_15m, 900, key="email")


def signup_limit() -> RateLimit:
    return RateLimit("signup", _s().rl_signup_per_hour, 3600, key="ip")


def refresh_limit() -> RateLimit:
    return RateLimit("refresh", _s().rl_refresh_per_minute, 60, key="ip")


def logout_limit() -> RateLimit:
    return RateLimit("logout", _s().rl_logout_per_minute, 60, key="ip")
