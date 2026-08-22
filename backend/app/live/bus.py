"""Redis pub/sub channel names and the two things published on them.

Every channel name embeds `user_id` before `device_id`. That ordering is a
second line of defence: a viewer subscribes using its own authenticated
user_id, never one taken from the client, so a channel name can only ever
address a device inside the caller's own tenant even if the RLS ownership
check upstream were ever bypassed.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from app.redis import get_redis
from app.schemas.protocol import Sample

logger = logging.getLogger(__name__)

ControlEvent = Literal["viewer_joined", "viewer_left"]


def metrics_channel(user_id: uuid.UUID, device_id: uuid.UUID) -> str:
    return f"sentinel:metrics:{user_id}:{device_id}"


def control_channel(user_id: uuid.UUID, device_id: uuid.UUID) -> str:
    return f"sentinel:control:{user_id}:{device_id}"


async def publish_samples(
    user_id: uuid.UUID, device_id: uuid.UUID, samples: list[Sample]
) -> None:
    """Fan out newly-accepted samples to any subscribed viewer.

    Called only after the durable write has committed, and only when the
    ingest supervisor's cached live-viewer count is nonzero — a batch with no
    viewer watching costs nothing here. Failures are logged and swallowed:
    the agent's ack must never depend on Redis being reachable.
    """
    if not samples:
        return
    payload = "[" + ",".join(s.model_dump_json() for s in samples) + "]"
    try:
        await get_redis().publish(metrics_channel(user_id, device_id), payload)
    except Exception:
        logger.warning(
            "live publish failed user_id=%s device_id=%s", user_id, device_id, exc_info=True
        )


async def publish_control(
    user_id: uuid.UUID, device_id: uuid.UUID, event: ControlEvent
) -> None:
    """Tell the ingest supervisor for this device that a viewer arrived or left.

    Best-effort: the supervisor's periodic reconcile tick is what makes the
    system correct even if this message is dropped, so a failure here is not
    fatal, only slower to converge.
    """
    try:
        await get_redis().publish(control_channel(user_id, device_id), event)
    except Exception:
        logger.warning(
            "control publish failed user_id=%s device_id=%s event=%s",
            user_id,
            device_id,
            event,
            exc_info=True,
        )
