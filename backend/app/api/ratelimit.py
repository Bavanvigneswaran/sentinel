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

import redis.asyncio as aioredis
from fastapi import HTTPException, Request, status

from app.config import get_settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None

KeyKind = Literal["ip", "email", "user"]


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def _client_ip(request: Request) -> str:
    # Only request.client is trusted. X-Forwarded-For is attacker-controlled
    # unless a known proxy sets it; in production run uvicorn with
    # --proxy-headers --forwarded-allow-ips=<lb> so Starlette populates
    # request.client correctly instead of trusting the header here.
    return request.client.host if request.client else "unknown"


def _hash(value: str) -> str:
    # Keeps plaintext email addresses out of Redis.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


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
            # Fail open. A Redis outage must not lock every user out of the
            # product. The tradeoff is real: brute-force protection is gone
            # while Redis is down, so production must alert on this log line.
            logger.error("rate limiter unavailable, failing open", exc_info=True)
            return

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
