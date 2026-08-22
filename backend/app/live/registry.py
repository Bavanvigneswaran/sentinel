"""Who is watching which device, right now, as a leased ZSET.

Pure pub/sub for "a viewer is watching" is the wrong primitive: a crashed
browser tab, a dropped pub/sub message, or a backend restart would leave an
agent stuck pushing 1s samples forever with no viewer to show them to. A lease
fixes that — membership expires on its own, so the supervisor's periodic
reconcile tick always converges to the truth even if every message was lost.

Score = the epoch second the lease expires. `LEASE_TTL_SECONDS` (30s) against
the viewer's 10s heartbeat gives two missed heartbeats of tolerance before a
socket falls out of the count.
"""

from __future__ import annotations

import time
import uuid

from app.redis import get_redis

LEASE_TTL_SECONDS = 30


def _live_key(user_id: uuid.UUID, device_id: uuid.UUID) -> str:
    return f"sentinel:live:{user_id}:{device_id}"


async def claim(user_id: uuid.UUID, device_id: uuid.UUID, viewer_id: str) -> None:
    """Register (or renew) one viewer's lease on one device."""
    key = _live_key(user_id, device_id)
    await get_redis().zadd(key, {viewer_id: time.time() + LEASE_TTL_SECONDS})


async def release(user_id: uuid.UUID, device_id: uuid.UUID, viewer_id: str) -> None:
    """Drop a viewer's lease immediately, rather than waiting for it to expire."""
    key = _live_key(user_id, device_id)
    await get_redis().zrem(key, viewer_id)


async def live_count(user_id: uuid.UUID, device_id: uuid.UUID) -> int:
    """Prune expired leases, then count what remains.

    Pruning on every read (rather than relying on a TTL per member, which
    ZSETs do not support) is what makes a crashed viewer or a lost 'viewer_left'
    message self-heal: the next reconcile tick sees the truth even though
    nothing explicitly cleaned up after it.
    """
    key = _live_key(user_id, device_id)
    redis = get_redis()
    await redis.zremrangebyscore(key, "-inf", time.time())
    return await redis.zcard(key)
