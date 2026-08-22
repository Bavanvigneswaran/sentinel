"""The shared Redis client.

One client for rate limiting (app/api/ratelimit.py) and the live pipeline
(app/live/). A single module so both import the same lazily-created connection
pool instead of each opening its own.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from app.config import get_settings

_redis: aioredis.Redis | None = None


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
