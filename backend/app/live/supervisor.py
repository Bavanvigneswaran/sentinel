"""Per-connection supervisor: decides when one agent should be in live mode.

One instance per agent WebSocket, created after the `hello` handshake and
cancelled in the connection's `finally`. It owns two independent jobs:

* Reconcile the desired push mode against `app.live.registry`'s lease count.
  Driven primarily by the control channel, so a viewer opening Live
  Monitoring upshifts the agent within roughly one round trip — but also on a
  periodic tick, so a lost pub/sub message, a viewer whose tab crashed
  without unsubscribing, or a backend restart all still converge to the
  correct mode within one tick. That tick is what makes the system
  self-healing rather than merely event-driven.
* Send the idle-connection keepalive `PingFrame` the protocol already defines
  but nothing was previously sending.

Every frame this supervisor writes to the socket goes through the same send
lock as the main receive loop in app/ingest/ws.py — Starlette does not
serialise concurrent `send_text` calls, and two frames interleaved mid-write
corrupt the stream for both.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from app.live import registry
from app.live.bus import control_channel
from app.redis import get_redis
from app.schemas.protocol import ModeFrame, PingFrame

logger = logging.getLogger(__name__)

#: Upper bound on how stale the live-viewer count can get if a control-channel
#: message is ever lost. Two of these against the viewer's 10s heartbeat
#: leaves comfortable room before a lease it renewed would even expire.
RECONCILE_INTERVAL_SECONDS = 15

#: Matches the agent's own `ping_interval` in transport/client.py; this is the
#: server-initiated half of the same keepalive contract.
PING_INTERVAL_SECONDS = 20

NORMAL_PUSH_INTERVAL_SECONDS = 10
LIVE_PUSH_INTERVAL_SECONDS = 1

SendFrame = Callable[[object], Awaitable[None]]


class LiveSupervisor:
    def __init__(
        self, *, user_id: uuid.UUID, device_id: uuid.UUID, send: SendFrame
    ) -> None:
        self._user_id = user_id
        self._device_id = device_id
        self._send = send
        self._mode = "normal"
        #: Read by ingest/ws.py before publishing a batch, so a device with no
        #: viewer costs zero extra Redis round trips per push.
        self.live_viewers = 0

    async def run(self) -> None:
        pubsub = get_redis().pubsub()
        await pubsub.subscribe(control_channel(self._user_id, self._device_id))
        try:
            await asyncio.gather(
                self._reconcile_loop(),
                self._control_listener(pubsub),
                self._ping_loop(),
            )
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    async def _reconcile_loop(self) -> None:
        while True:
            await self._reconcile()
            await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)

    async def _control_listener(self, pubsub) -> None:  # noqa: ANN001
        # get_message-based polling rather than `async for ... in pubsub.listen()`:
        # listen() blocks indefinitely on the network read, which on some
        # redis-py versions swallows task cancellation until a message
        # actually arrives. A short poll keeps cancellation (connection
        # closing) responsive.
        while True:
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            except Exception:
                logger.warning("control listener lost its pubsub connection", exc_info=True)
                await asyncio.sleep(1.0)
                continue
            if message is not None:
                await self._reconcile()

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            await self._send(PingFrame())

    async def _reconcile(self) -> None:
        try:
            count = await registry.live_count(self._user_id, self._device_id)
        except Exception:
            # A Redis blip must not flip a live viewer into a dead one; hold
            # the current mode and let the next tick retry.
            logger.warning("live_count failed device_id=%s", self._device_id, exc_info=True)
            return

        self.live_viewers = count
        desired = "live" if count > 0 else "normal"
        if desired == self._mode:
            return

        self._mode = desired
        interval = (
            LIVE_PUSH_INTERVAL_SECONDS if desired == "live" else NORMAL_PUSH_INTERVAL_SECONDS
        )
        await self._send(ModeFrame(mode=desired, push_interval_seconds=interval))
        logger.info(
            "device_id=%s switched to %s mode (%d viewer(s))",
            self._device_id,
            desired,
            count,
        )
