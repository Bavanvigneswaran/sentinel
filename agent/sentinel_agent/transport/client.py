"""The outbound WebSocket client.

Reconnects with exponential backoff plus jitter. Jitter matters at fleet scale:
without it, every agent that lost a connection to the same backend retries in
lockstep and hammers it back down the moment it recovers.

A batch stays in the buffer until the server acks it, so a connection that
drops mid-push loses nothing. The server's writes are idempotent, so redelivery
after a lost ack is harmless.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid

import websockets
from websockets.exceptions import InvalidStatus

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1

INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0
BACKOFF_FACTOR = 2.0
#: Full jitter. Cuts the thundering herd when a backend comes back up.
JITTER = 0.5


class FatalTransportError(Exception):
    """The server told us not to bother retrying — a revoked token, or a
    protocol version it will never accept."""


def next_backoff(attempt: int) -> float:
    base = min(INITIAL_BACKOFF_SECONDS * (BACKOFF_FACTOR**attempt), MAX_BACKOFF_SECONDS)
    return base * (1 - JITTER * random.random())  # noqa: S311 — jitter, not crypto


class AgentConnection:
    """One live socket. Owns the handshake and the send/ack cycle."""

    def __init__(self, ws) -> None:  # noqa: ANN001
        self._ws = ws
        self.mode: str = "normal"
        self.push_interval_seconds: int = 10
        self.device_id: str | None = None

    async def handshake(self, host_info: dict) -> None:
        await self._send({
            "type": "hello",
            "protocol_version": PROTOCOL_VERSION,
            "host": host_info,
        })
        frame = await self._receive()

        if frame.get("type") == "error":
            message = f"{frame.get('code')}: {frame.get('message')}"
            if not frame.get("retryable", True):
                raise FatalTransportError(message)
            raise ConnectionError(message)

        if frame.get("type") != "welcome":
            raise FatalTransportError(f"expected welcome, got {frame.get('type')!r}")

        self.device_id = frame.get("device_id")
        self.mode = frame.get("mode", "normal")
        self.push_interval_seconds = frame.get("push_interval_seconds", 10)
        logger.info(
            "connected device_id=%s mode=%s interval=%ss",
            self.device_id, self.mode, self.push_interval_seconds,
        )

    async def push(self, samples: list[dict]) -> int:
        """Send a batch and wait for its ack. Returns the accepted count."""
        batch_id = uuid.uuid4().hex
        await self._send({"type": "metrics", "batch_id": batch_id, "samples": samples})

        while True:
            frame = await self._receive()
            kind = frame.get("type")

            if kind == "ack":
                if frame.get("batch_id") != batch_id:
                    continue
                rejected = frame.get("rejected", 0)
                if rejected:
                    # Out-of-window samples. Retrying would never succeed.
                    logger.warning("server rejected %d stale sample(s)", rejected)
                return int(frame.get("accepted", 0))

            if kind == "mode":
                self._apply_mode(frame)
                continue

            if kind == "ping":
                await self._send({"type": "pong"})
                continue

            if kind == "error":
                message = f"{frame.get('code')}: {frame.get('message')}"
                if not frame.get("retryable", True):
                    raise FatalTransportError(message)
                raise ConnectionError(message)

    async def idle(self, seconds: float) -> None:
        """Wait out the push interval, staying responsive to server frames.

        A blind `sleep(push_interval)` here is what made Live Monitoring take
        up to a full push interval to start moving. The supervisor sends the
        `mode` frame within a round trip of a viewer opening the page, but
        nothing was reading the socket to see it: the agent was already inside
        a 10s sleep, and **changing an interval does not shorten a sleep that
        has already started.** The next push therefore went out at the old
        cadence *and* was built in the old mode — one 10s-collapsed row —
        before live mode took effect on the iteration after that.

        Exactly the bug Phase 10b found in the Kotlin collector, reached from
        the other side: there the sampler slept, here the pusher does.

        Returns as soon as the cadence actually changes, so the caller pushes
        the buffered 1s samples immediately instead of waiting out an interval
        that no longer applies.
        """
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                frame = await asyncio.wait_for(self._receive(), timeout=remaining)
            except TimeoutError:
                return

            kind = frame.get("type")
            if kind == "mode":
                before = self.push_interval_seconds
                self._apply_mode(frame)
                # Only a cadence change is worth cutting the wait short for.
                # Re-entering the push loop for a mode frame that changed
                # nothing would just rebuild the same batch a moment early.
                if self.push_interval_seconds != before:
                    return
            elif kind == "ping":
                await self._send({"type": "pong"})
            elif kind == "error":
                # Same handling as the ack path: a non-retryable error is
                # fatal wherever it arrives, and swallowing one here would
                # leave the agent idling against a socket the server has
                # already given up on.
                message = f"{frame.get('code')}: {frame.get('message')}"
                if not frame.get("retryable", True):
                    raise FatalTransportError(message)
                raise ConnectionError(message)

    def _apply_mode(self, frame: dict) -> None:
        self.mode = frame.get("mode", "normal")
        self.push_interval_seconds = frame.get(
            "push_interval_seconds", self.push_interval_seconds
        )
        logger.info("switched to %s mode (%ss)", self.mode, self.push_interval_seconds)

    async def _send(self, payload: dict) -> None:
        await self._ws.send(json.dumps(payload, default=str))

    async def _receive(self) -> dict:
        raw = await self._ws.recv()
        return json.loads(raw)


async def connect(url: str, token: str) -> AgentConnection:
    """Open an authenticated socket.

    The token travels in the handshake's Authorization header rather than the
    URL: query strings end up in proxy logs and browser history.
    """
    try:
        ws = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {token}"},
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            max_size=1024 * 1024,
        )
    except InvalidStatus as exc:
        # 401/403 at the handshake means the token is dead. Reconnecting on a
        # loop would just be a slow brute force against our own server.
        if exc.response.status_code in (401, 403):
            raise FatalTransportError(
                "server rejected the agent token — it may have been revoked. "
                "Re-enrol with `sentinel-agent enroll --code <CODE>`."
            ) from exc
        raise

    return AgentConnection(ws)
