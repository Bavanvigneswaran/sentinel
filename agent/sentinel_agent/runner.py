"""The agent's main loop.

Sample at a fixed cadence into a ring buffer; push on the interval the server
asked for. Sampling continues while the socket is down, so a reconnect delivers
the gap rather than a hole in the chart.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from sentinel_agent.buffer import SampleBuffer, aggregate
from sentinel_agent.collectors import latency as latency_mod
from sentinel_agent.collectors.host import collect_host_info
from sentinel_agent.collectors.resources import (
    DiskIoCollector,
    NetCollector,
    collect_disk_usage,
    collect_processes,
)
from sentinel_agent.collectors.system import SystemCollector
from sentinel_agent.config import AgentConfig
from sentinel_agent.transport.client import (
    FatalTransportError,
    connect,
    next_backoff,
)

logger = logging.getLogger(__name__)

#: Matches MAX_SAMPLES_PER_BATCH in the server's protocol schema. A backlog
#: larger than this is delivered across consecutive pushes.
MAX_SAMPLES_PER_BATCH = 240

#: Latency probes involve network round trips, so they run on their own slower
#: cadence rather than blocking every 1s sample.
LATENCY_INTERVAL_SECONDS = 30


class Agent:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.buffer = SampleBuffer(maxlen=config.buffer_size)

        self._system = SystemCollector()
        self._disk_io = DiskIoCollector()
        self._net = NetCollector()
        self._targets = [latency_mod.LatencyTarget.parse(t) for t in config.latency_targets]

        self._latest_latency: list[dict] = []
        self._last_latency_at = 0.0
        self._stopping = asyncio.Event()

    # --- sampling ----------------------------------------------------------

    async def sample_once(self) -> dict:
        now = time.monotonic()

        if now - self._last_latency_at >= LATENCY_INTERVAL_SECONDS:
            self._last_latency_at = now
            self._latest_latency = await latency_mod.measure_all(self._targets)

        return {
            "ts": datetime.now(UTC).isoformat(),
            "system": self._system.collect(),
            "disk_usage": collect_disk_usage(),
            "disk_io": self._disk_io.collect(now),
            "net": self._net.collect(now),
            # Carried between probes so every sample has the latest known value
            # rather than gaps; the value itself is always a real measurement.
            "latency": self._latest_latency,
            "processes": collect_processes() if self.config.collect_processes else [],
        }

    async def _sample_loop(self) -> None:
        while not self._stopping.is_set():
            started = time.monotonic()
            try:
                self.buffer.add(await self.sample_once())
            except Exception:
                logger.exception("sampling failed; continuing")
            # Subtract the work already done so the cadence does not drift.
            delay = max(0.0, self.config.sample_interval_seconds - (time.monotonic() - started))
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=delay)
            except TimeoutError:
                pass

    # --- pushing -----------------------------------------------------------

    def _build_batch(self, raw: list[dict], mode: str) -> tuple[list[dict], int]:
        """Return (frames_to_send, raw_samples_consumed).

        The consumed count is what gets discarded once the server acks, so a
        backlog larger than one batch is delivered over several pushes instead
        of being thrown away.
        """
        if mode == "live":
            # Live monitoring wants every 1s point, unaggregated.
            consumed = raw[:MAX_SAMPLES_PER_BATCH]
            return (
                [
                    {**s, "resolution_seconds": self.config.sample_interval_seconds}
                    for s in consumed
                ],
                len(consumed),
            )

        # Chunk into push-interval-sized windows. Collapsing a whole backlog
        # into one row would claim resolution_seconds=10 for a sample that
        # actually spans the entire outage — an hour of history flattened into
        # a single point that never happened.
        window = max(
            1, self.config.push_interval_seconds // max(self.config.sample_interval_seconds, 1)
        )

        frames: list[dict] = []
        consumed = 0
        for start in range(0, len(raw), window):
            if len(frames) >= MAX_SAMPLES_PER_BATCH:
                break
            chunk = raw[start : start + window]
            # Hold back a partial trailing window unless it is all we have;
            # otherwise every push would emit a short, unrepresentative sample.
            if len(chunk) < window and start > 0:
                break
            collapsed = aggregate(chunk, resolution_seconds=self.config.push_interval_seconds)
            if collapsed:
                frames.append(collapsed)
                consumed += len(chunk)
        return frames, consumed

    async def _push_loop(self, connection) -> None:  # noqa: ANN001
        while not self._stopping.is_set():
            interval = connection.push_interval_seconds
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)
                return
            except TimeoutError:
                pass

            raw = self.buffer.peek()
            if not raw:
                await connection.listen_for_mode()
                continue

            batch, consumed = self._build_batch(raw, connection.mode)
            if not batch:
                continue

            accepted = await connection.push(batch)
            # Discard only after the ack, and only what this batch covered. A
            # clear-everything would lose samples collected during the push and
            # would drop a backlog the server never received.
            self.buffer.discard(consumed)
            logger.debug(
                "pushed %d frame(s) from %d sample(s), %d accepted; %d still buffered",
                len(batch), consumed, accepted, len(self.buffer),
            )

    # --- supervision -------------------------------------------------------

    async def run(self) -> None:
        token = self.config.require_token()
        host_info = collect_host_info()

        sampler = asyncio.create_task(self._sample_loop())
        attempt = 0

        try:
            while not self._stopping.is_set():
                try:
                    connection = await connect(self.config.ws_url, token)
                    await connection.handshake(host_info)
                    attempt = 0  # a successful handshake resets the backoff
                    await self._push_loop(connection)
                except FatalTransportError as exc:
                    logger.error("%s", exc)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    delay = next_backoff(attempt)
                    attempt += 1
                    logger.warning(
                        "connection lost (%s); reconnecting in %.1fs", exc, delay
                    )
                    try:
                        await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                    except TimeoutError:
                        continue
        finally:
            self._stopping.set()
            sampler.cancel()
            try:
                await sampler
            except asyncio.CancelledError:
                pass

    def stop(self) -> None:
        self._stopping.set()
