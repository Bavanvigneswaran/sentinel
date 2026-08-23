"""The agent ingest WebSocket.

Agents dial in here and stay connected. The socket is the only thing they ever
need — no inbound ports, no polling.

Everything arriving on this socket is untrusted. An agent token lives on a
machine we do not control, so every frame is size-capped before parsing and
schema-validated before it reaches the database.

Phase 3 adds a `LiveSupervisor` task per connection, running concurrently with
the receive loop below, to upshift/downshift push cadence and publish accepted
samples to any subscribed viewer. See app/live/supervisor.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import TypeAdapter, ValidationError

from app.db import AdminSessionLocal
from app.ingest.auth import AgentIdentity, authenticate_agent, extract_token
from app.ingest.writer import write_samples
from app.live.bus import publish_samples
from app.live.supervisor import LiveSupervisor
from app.models import Device
from app.schemas.protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    AckFrame,
    AgentFrame,
    ErrorFrame,
    HelloFrame,
    MetricsFrame,
    PongFrame,
    WelcomeFrame,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_agent_frame = TypeAdapter(AgentFrame)

#: Default push cadence. LiveSupervisor sends a ModeFrame to switch an agent
#: to LIVE_PUSH_INTERVAL_SECONDS while a viewer is watching.
NORMAL_PUSH_INTERVAL_SECONDS = 10
LIVE_PUSH_INTERVAL_SECONDS = 1

#: Only touch last_seen_at this often. At 1s live push this would otherwise be
#: one UPDATE per second per device; the same reasoning as
#: LAST_USED_REFRESH_SECONDS in app/ingest/auth.py.
LAST_SEEN_TOUCH_THROTTLE_SECONDS = 15


class _Sender:
    """Serialises writes to the agent socket.

    Both the receive loop below (welcome/ack/error) and the LiveSupervisor
    task (mode changes, keepalive pings) write to the same WebSocket
    concurrently. Starlette does not serialise concurrent `send_text` calls,
    and two frames interleaved mid-write would corrupt the stream for an
    agent that parses each message as one JSON object.
    """

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._lock = asyncio.Lock()

    async def __call__(self, frame) -> None:  # noqa: ANN001
        async with self._lock:
            await self._ws.send_text(frame.model_dump_json())

    async def close(self, code: int) -> None:
        await self._ws.close(code=code)


async def _reject(sender: _Sender, code: str, message: str, *, retryable: bool) -> None:
    """Tell the agent why before closing, so it can back off intelligently
    instead of hammering a socket that will never accept it."""
    # Constructed outside the try on purpose. `code` is a closed Literal on
    # ErrorFrame, so an unlisted value raises here — and inside the try that
    # became a swallowed ValidationError which sent the agent a bare 1008 with
    # no reason at all. A wrong code is our bug, not a peer that went away, and
    # only the latter is what the try below is for.
    frame = ErrorFrame(code=code, message=message, retryable=retryable)
    try:
        await sender(frame)
    except Exception:
        # The peer may already be gone; the close below is what matters.
        logger.debug("could not deliver error frame", exc_info=True)
    await sender.close(status.WS_1008_POLICY_VIOLATION)


async def _apply_host_info(identity: AgentIdentity, hello: HelloFrame) -> None:
    """Record what the agent says about its host.

    Scoped by user_id as well as device_id: defence in depth behind the token's
    own device binding.
    """
    host = hello.host
    async with AdminSessionLocal() as session:
        import sqlalchemy as sa

        await session.execute(
            sa.update(Device)
            .where(Device.id == identity.device_id, Device.user_id == identity.user_id)
            .values(
                hostname=host.hostname,
                os=host.os,
                os_version=host.os_version,
                kernel_version=host.kernel_version,
                arch=host.arch,
                cpu_cores=host.cpu_cores,
                total_memory_bytes=host.total_memory_bytes,
                agent_version=host.agent_version,
                platform=host.platform,
                status="online",
                last_seen_at=datetime.now(UTC),
                enrolled_at=sa.func.coalesce(Device.enrolled_at, sa.func.now()),
            )
        )
        await session.commit()


async def _mark_offline(identity: AgentIdentity) -> None:
    import sqlalchemy as sa

    async with AdminSessionLocal() as session:
        await session.execute(
            sa.update(Device)
            .where(Device.id == identity.device_id, Device.user_id == identity.user_id)
            .values(status="offline", last_seen_at=datetime.now(UTC))
        )
        await session.commit()


async def _touch_last_seen(identity: AgentIdentity) -> bool:
    """Record the agent as alive, and answer whether it is still allowed to be.

    Returns False once the device has been deleted or the token revoked.

    The authorization re-check rides on this existing periodic write rather
    than being a second query on its own timer, because the two answer the same
    question — is this still a device we accept data from — and doing it in one
    statement means the "yes" costs exactly what it did before.

    Authentication happens once, at the handshake. Without this, revoking a
    token or deleting a device had no effect on a socket that was *already
    open*: the credential was dead and the session was not, so a removed device
    kept ingesting metrics indefinitely — until the agent happened to
    reconnect, which for a healthy desktop agent may be days. Found by removing
    a phone from the app and watching its collector keep pushing, with the
    device row soft-deleted and its token revoked. `DELETE /devices/{id}`
    already says in its own docstring that a delete leaving a working
    credential behind is not a delete; the same is true of a live session.
    """
    import sqlalchemy as sa

    from app.models import AgentToken

    async with AdminSessionLocal() as session:
        result = await session.execute(
            sa.update(Device)
            .where(
                Device.id == identity.device_id,
                Device.user_id == identity.user_id,
                Device.deleted_at.is_(None),
                sa.exists().where(
                    AgentToken.id == identity.token_id,
                    AgentToken.revoked_at.is_(None),
                ),
            )
            .values(last_seen_at=datetime.now(UTC), status="online")
        )
        await session.commit()
        return result.rowcount > 0


@router.websocket("/ws/agent")
async def agent_socket(websocket: WebSocket) -> None:
    # Authenticate before accepting: a rejected token should never get a live
    # socket. The handshake carries the header because the agent is a native
    # client, not a browser.
    token = extract_token(websocket.headers.get("authorization"))
    if token is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with AdminSessionLocal() as session:
        identity = await authenticate_agent(session, token)

    if identity is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    sender = _Sender(websocket)

    try:
        await _run_session(websocket, identity, sender)
    except WebSocketDisconnect:
        logger.info("agent disconnected device_id=%s", identity.device_id)
    except Exception:
        logger.exception("agent session failed device_id=%s", identity.device_id)
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        except Exception:
            logger.debug("socket already closed", exc_info=True)
    finally:
        await _mark_offline(identity)


async def _run_session(websocket: WebSocket, identity: AgentIdentity, sender: _Sender) -> None:
    handshaken = False
    last_touch: datetime | None = None
    supervisor: LiveSupervisor | None = None
    supervisor_task: asyncio.Task | None = None

    try:
        while True:
            raw = await websocket.receive_text()

            # Cap before parsing, not after: the point is to never build a large
            # object graph from an untrusted frame.
            if len(raw) > MAX_FRAME_BYTES:
                await _reject(
                    sender, "too_large", "frame exceeds the maximum size", retryable=False
                )
                return

            try:
                frame = _agent_frame.validate_json(raw)
            except ValidationError as exc:
                logger.warning(
                    "invalid frame from device_id=%s: %s", identity.device_id, exc.error_count()
                )
                await _reject(
                    sender, "invalid_frame", "frame failed validation", retryable=False
                )
                return

            if isinstance(frame, HelloFrame):
                if frame.protocol_version != PROTOCOL_VERSION:
                    await _reject(
                        sender,
                        "protocol_version",
                        f"server speaks protocol {PROTOCOL_VERSION}",
                        retryable=False,
                    )
                    return
                await _apply_host_info(identity, frame)
                await sender(
                    WelcomeFrame(
                        device_id=str(identity.device_id),
                        mode="normal",
                        push_interval_seconds=NORMAL_PUSH_INTERVAL_SECONDS,
                    )
                )
                handshaken = True
                last_touch = datetime.now(UTC)
                # Only started once welcome has been sent: the agent's
                # handshake() expects exactly one reply — a 'welcome' frame —
                # to its hello, and would treat an earlier unsolicited 'mode'
                # or 'ping' as a fatal protocol violation.
                supervisor = LiveSupervisor(
                    user_id=identity.user_id, device_id=identity.device_id, send=sender
                )
                supervisor_task = asyncio.create_task(supervisor.run())
                continue

            if not handshaken:
                # Metrics before hello would mean writing samples for a device whose
                # host details we have never confirmed.
                await _reject(
                    sender, "invalid_frame", "expected hello first", retryable=False
                )
                return

            if isinstance(frame, MetricsFrame):
                async with AdminSessionLocal() as session:
                    result = await write_samples(
                        session,
                        device_id=identity.device_id,
                        user_id=identity.user_id,
                        samples=frame.samples,
                    )

                now = datetime.now(UTC)
                if (
                    last_touch is None
                    or (now - last_touch).total_seconds() > LAST_SEEN_TOUCH_THROTTLE_SECONDS
                ):
                    if not await _touch_last_seen(identity):
                        # Not retryable: the agent should stop rather than
                        # reconnect. Its token is gone, so a reconnect would
                        # fail the handshake anyway — saying so plainly beats
                        # letting it back off against a door that is locked.
                        await _reject(
                            sender,
                            "unauthorized",
                            "this device has been removed or its token revoked",
                            retryable=False,
                        )
                        return
                    last_touch = now

                # The durable write already happened; publishing is a
                # best-effort convenience and is skipped entirely when the
                # supervisor's cached lease count says nobody is watching.
                if supervisor is not None and supervisor.live_viewers > 0:
                    await publish_samples(
                        identity.user_id, identity.device_id, result.accepted_samples
                    )

                await sender(
                    AckFrame(
                        batch_id=frame.batch_id,
                        accepted=result.accepted,
                        rejected=result.rejected,
                    )
                )
                continue

            if isinstance(frame, PongFrame):
                continue
    finally:
        if supervisor_task is not None:
            supervisor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await supervisor_task
