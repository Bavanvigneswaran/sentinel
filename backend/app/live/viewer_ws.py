"""The viewer WebSocket.

A logged-in browser subscribes to one or more of its own devices and receives
the same `Sample` objects the agent pushed, fanned out over Redis by
app/live/bus.py. Authenticated by a single-use ticket (app/live/tickets.py)
rather than the access JWT: a browser cannot set an `Authorization` header on
a WebSocket handshake, and a 15-minute bearer token has no business in a
query string.

Every subscribed device gets a lease in app/live/registry.py — so the agent's
LiveSupervisor sees a viewer and upshifts — and a Redis pub/sub subscription
on this socket's one shared connection, dispatched back out by device_id.
Both are torn down on unsubscribe or disconnect.

A browser tab that falls behind must see *now*, not a delayed replay: the
outbound queue is bounded, and overflow drops the oldest sample rather than
blocking or buffering.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import TypeAdapter, ValidationError

from app.db import SessionLocal, scope_to_user
from app.live import registry
from app.live.bus import metrics_channel, publish_control
from app.live.tickets import redeem_ticket
from app.models import Device
from app.redis import get_redis
from app.schemas.devices import derive_device_status
from app.schemas.live import (
    MAX_SUBSCRIPTIONS_PER_SOCKET,
    MAX_VIEWER_FRAME_BYTES,
    DeviceStatusFrame,
    SamplesFrame,
    SubscribedFrame,
    SubscribeFrame,
    UnsubscribeFrame,
    ViewerErrorFrame,
    ViewerFrame,
    ViewerPingFrame,
    ViewerPongFrame,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_viewer_frame = TypeAdapter(ViewerFrame)

#: Matches the lease's LEASE_TTL_SECONDS / 3, giving two missed heartbeats of
#: tolerance before the agent-side supervisor drops this viewer.
LEASE_RENEW_INTERVAL_SECONDS = 10
STATUS_POLL_INTERVAL_SECONDS = 10
VIEWER_PING_INTERVAL_SECONDS = 20

#: A tab that stalls should see *now* on reconnect, not a queued backlog.
OUTBOUND_QUEUE_MAXSIZE = 64


class _Sender:
    """Serialises writes to the viewer socket, same reasoning as the ingest
    socket's _Sender: several tasks (frame dispatch, pings, status changes)
    write concurrently, and Starlette does not serialise `send_text` calls."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._lock = asyncio.Lock()

    async def __call__(self, frame) -> None:  # noqa: ANN001
        async with self._lock:
            await self._ws.send_text(frame.model_dump_json())

    async def close(self, code: int) -> None:
        await self._ws.close(code=code)


class ViewerSession:
    """One browser socket's subscriptions, leases, and background tasks."""

    def __init__(self, websocket: WebSocket, user_id: uuid.UUID) -> None:
        self.sender = _Sender(websocket)
        self.user_id = user_id
        #: Unique per socket, not per user — a second tab is a second viewer.
        self.viewer_id = uuid.uuid4().hex
        self.subscriptions: dict[uuid.UUID, str] = {}
        self._channel_to_device: dict[str, uuid.UUID] = {}
        self._pubsub = get_redis().pubsub()
        self._queue: asyncio.Queue[tuple[uuid.UUID, str]] = asyncio.Queue(
            maxsize=OUTBOUND_QUEUE_MAXSIZE
        )
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._forward_loop()),
            asyncio.create_task(self._drain_loop()),
            asyncio.create_task(self._lease_renew_loop()),
            asyncio.create_task(self._status_poll_loop()),
            asyncio.create_task(self._ping_loop()),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for device_id in list(self.subscriptions):
            await self._teardown_subscription(device_id)
        with contextlib.suppress(Exception):
            await self._pubsub.aclose()

    # --- subscription lifecycle ---------------------------------------------

    async def subscribe(self, device_id: uuid.UUID) -> None:
        if device_id in self.subscriptions:
            return
        if len(self.subscriptions) >= MAX_SUBSCRIPTIONS_PER_SOCKET:
            await self.sender(
                ViewerErrorFrame(
                    code="too_many_subscriptions",
                    message=f"at most {MAX_SUBSCRIPTIONS_PER_SOCKET} devices per socket",
                    device_id=device_id,
                )
            )
            return

        device_status = await self._lookup_status(device_id)
        if device_status is None:
            await self.sender(
                ViewerErrorFrame(code="not_found", message="device not found", device_id=device_id)
            )
            return

        self.subscriptions[device_id] = device_status
        channel = metrics_channel(self.user_id, device_id)
        self._channel_to_device[channel] = device_id
        await self._pubsub.subscribe(channel)
        await registry.claim(self.user_id, device_id, self.viewer_id)
        await publish_control(self.user_id, device_id, "viewer_joined")
        await self.sender(
            SubscribedFrame(device_id=device_id, online=(device_status == "online"))
        )

    async def unsubscribe(self, device_id: uuid.UUID) -> None:
        if device_id not in self.subscriptions:
            return
        await self._teardown_subscription(device_id)

    async def _teardown_subscription(self, device_id: uuid.UUID) -> None:
        self.subscriptions.pop(device_id, None)
        channel = metrics_channel(self.user_id, device_id)
        self._channel_to_device.pop(channel, None)
        with contextlib.suppress(Exception):
            await self._pubsub.unsubscribe(channel)
        with contextlib.suppress(Exception):
            await registry.release(self.user_id, device_id, self.viewer_id)
        with contextlib.suppress(Exception):
            await publish_control(self.user_id, device_id, "viewer_left")

    async def _lookup_status(self, device_id: uuid.UUID) -> str | None:
        """RLS-scoped ownership check, doubling as the status lookup.

        A miss here is "not yours" and "not there" indistinguishably — same
        reasoning as the enrollment-code lookup in api/routes/devices.py.
        """
        async with SessionLocal() as session:
            scope_to_user(session, self.user_id)
            row = (
                await session.execute(
                    sa.select(Device.status, Device.last_seen_at, Device.deleted_at).where(
                        Device.id == device_id
                    )
                )
            ).one_or_none()
        if row is None or row.deleted_at is not None:
            return None
        return derive_device_status(row.status, row.last_seen_at)

    # --- background tasks ----------------------------------------------------

    async def _forward_loop(self) -> None:
        """Read fanned-out samples off Redis and queue them for delivery."""
        while True:
            if not self._channel_to_device:
                # redis-py raises rather than blocking/timing out when
                # get_message() is called on a pubsub with zero subscriptions
                # — a normal state for the window between accept() and the
                # browser's first `subscribe` frame.
                await asyncio.sleep(0.2)
                continue
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
            except Exception:
                logger.warning("viewer pubsub read failed", exc_info=True)
                await asyncio.sleep(1.0)
                continue
            if message is None:
                continue
            device_id = self._channel_to_device.get(message["channel"])
            if device_id is None:
                continue
            self._enqueue(device_id, message["data"])

    def _enqueue(self, device_id: uuid.UUID, raw: str) -> None:
        try:
            self._queue.put_nowait((device_id, raw))
        except asyncio.QueueFull:
            # Drop the oldest, not the newest — a lagging viewer should catch
            # up to "now", not slowly replay a growing backlog.
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait((device_id, raw))

    async def _drain_loop(self) -> None:
        while True:
            device_id, raw = await self._queue.get()
            samples = json.loads(raw)
            await self.sender(SamplesFrame(device_id=device_id, samples=samples))

    async def _lease_renew_loop(self) -> None:
        while True:
            await asyncio.sleep(LEASE_RENEW_INTERVAL_SECONDS)
            for device_id in list(self.subscriptions):
                with contextlib.suppress(Exception):
                    await registry.claim(self.user_id, device_id, self.viewer_id)

    async def _status_poll_loop(self) -> None:
        while True:
            await asyncio.sleep(STATUS_POLL_INTERVAL_SECONDS)
            for device_id in list(self.subscriptions):
                new_status = await self._lookup_status(device_id)
                if new_status is None or new_status == self.subscriptions.get(device_id):
                    continue
                self.subscriptions[device_id] = new_status
                await self.sender(DeviceStatusFrame(device_id=device_id, status=new_status))

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(VIEWER_PING_INTERVAL_SECONDS)
            await self.sender(ViewerPingFrame())


@router.websocket("/ws/viewer")
async def viewer_socket(websocket: WebSocket) -> None:
    ticket = websocket.query_params.get("ticket")
    if not ticket:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user_id = await redeem_ticket(ticket)
    if user_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    session = ViewerSession(websocket, user_id)
    await session.start()

    try:
        while True:
            raw = await websocket.receive_text()

            if len(raw) > MAX_VIEWER_FRAME_BYTES:
                await session.sender(
                    ViewerErrorFrame(code="too_large", message="frame exceeds the maximum size")
                )
                await session.sender.close(status.WS_1008_POLICY_VIOLATION)
                return

            try:
                frame = _viewer_frame.validate_json(raw)
            except ValidationError:
                await session.sender(
                    ViewerErrorFrame(code="invalid_frame", message="frame failed validation")
                )
                await session.sender.close(status.WS_1008_POLICY_VIOLATION)
                return

            if isinstance(frame, SubscribeFrame):
                await session.subscribe(frame.device_id)
            elif isinstance(frame, UnsubscribeFrame):
                await session.unsubscribe(frame.device_id)
            elif isinstance(frame, ViewerPongFrame):
                continue
    except WebSocketDisconnect:
        logger.info("viewer disconnected user_id=%s", user_id)
    except Exception:
        logger.exception("viewer session failed user_id=%s", user_id)
        with contextlib.suppress(Exception):
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
    finally:
        await session.stop()
