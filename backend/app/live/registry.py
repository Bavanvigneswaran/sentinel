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

import math
import time
import uuid

from app.redis import get_redis

LEASE_TTL_SECONDS = 30


def _live_key(user_id: uuid.UUID, device_id: uuid.UUID) -> str:
    return f"sentinel:live:{user_id}:{device_id}"


async def claim(user_id: uuid.UUID, device_id: uuid.UUID, viewer_id: str) -> None:
    """Register (or renew) one viewer's lease on one device.

    The key itself is given a TTL alongside the member's own expiry score.
    Pruning in `live_count()` deletes the ZSET once its last member expires,
    but only an agent's supervisor ever calls that — so a viewer that watched
    a device whose agent never reconnected left the key behind with nothing
    to ever clean it up. The TTL is refreshed on every renewal, so it can only
    fire once no viewer has claimed for a full lease period.
    """
    key = _live_key(user_id, device_id)
    redis = get_redis()
    async with redis.pipeline(transaction=True) as pipe:
        pipe.zadd(key, {viewer_id: time.time() + LEASE_TTL_SECONDS})
        # EXPIRE takes whole seconds, and tests shorten the lease to a
        # fraction of one; round up so the key always outlives the member
        # score it holds rather than truncating to a 0 Redis rejects.
        pipe.expire(key, max(1, math.ceil(LEASE_TTL_SECONDS)))
        await pipe.execute()


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
