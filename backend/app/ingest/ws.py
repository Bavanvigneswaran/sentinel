"""The agent ingest WebSocket.

Agents dial in here and stay connected. The socket is the only thing they ever
need — no inbound ports, no polling.

Everything arriving on this socket is untrusted. An agent token lives on a
machine we do not control, so every frame is size-capped before parsing and
schema-validated before it reaches the database.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import TypeAdapter, ValidationError

from app.db import AdminSessionLocal
from app.ingest.auth import AgentIdentity, authenticate_agent, extract_token
from app.ingest.writer import write_samples
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

#: Default push cadence. Live mode (1s) is switched on in Phase 3 when a viewer
#: opens the monitoring page.
NORMAL_PUSH_INTERVAL_SECONDS = 10
LIVE_PUSH_INTERVAL_SECONDS = 1


async def _send(ws: WebSocket, frame) -> None:  # noqa: ANN001
    await ws.send_text(frame.model_dump_json())


async def _reject(ws: WebSocket, code: str, message: str, *, retryable: bool) -> None:
    """Tell the agent why before closing, so it can back off intelligently
    instead of hammering a socket that will never accept it."""
    try:
        await _send(ws, ErrorFrame(code=code, message=message, retryable=retryable))
    except Exception:
        # The peer may already be gone; the close below is what matters.
        logger.debug("could not deliver error frame", exc_info=True)
    await ws.close(code=status.WS_1008_POLICY_VIOLATION)


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


async def _touch_last_seen(identity: AgentIdentity) -> None:
    import sqlalchemy as sa

    async with AdminSessionLocal() as session:
        await session.execute(
            sa.update(Device)
            .where(Device.id == identity.device_id, Device.user_id == identity.user_id)
            .values(last_seen_at=datetime.now(UTC), status="online")
        )
        await session.commit()


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

    try:
        await _run_session(websocket, identity)
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


async def _run_session(websocket: WebSocket, identity: AgentIdentity) -> None:
    handshaken = False

    while True:
        raw = await websocket.receive_text()

        # Cap before parsing, not after: the point is to never build a large
        # object graph from an untrusted frame.
        if len(raw) > MAX_FRAME_BYTES:
            await _reject(
                websocket, "too_large", "frame exceeds the maximum size", retryable=False
            )
            return

        try:
            frame = _agent_frame.validate_json(raw)
        except ValidationError as exc:
            logger.warning(
                "invalid frame from device_id=%s: %s", identity.device_id, exc.error_count()
            )
            await _reject(websocket, "invalid_frame", "frame failed validation", retryable=False)
            return

        if isinstance(frame, HelloFrame):
            if frame.protocol_version != PROTOCOL_VERSION:
                await _reject(
                    websocket,
                    "protocol_version",
                    f"server speaks protocol {PROTOCOL_VERSION}",
                    retryable=False,
                )
                return
            await _apply_host_info(identity, frame)
            await _send(
                websocket,
                WelcomeFrame(
                    device_id=str(identity.device_id),
                    mode="normal",
                    push_interval_seconds=NORMAL_PUSH_INTERVAL_SECONDS,
                ),
            )
            handshaken = True
            continue

        if not handshaken:
            # Metrics before hello would mean writing samples for a device whose
            # host details we have never confirmed.
            await _reject(websocket, "invalid_frame", "expected hello first", retryable=False)
            return

        if isinstance(frame, MetricsFrame):
            async with AdminSessionLocal() as session:
                result = await write_samples(
                    session,
                    device_id=identity.device_id,
                    user_id=identity.user_id,
                    samples=frame.samples,
                )
            await _touch_last_seen(identity)
            await _send(
                websocket,
                AckFrame(
                    batch_id=frame.batch_id,
                    accepted=result.accepted,
                    rejected=result.rejected,
                ),
            )
            continue

        if isinstance(frame, PongFrame):
            continue
