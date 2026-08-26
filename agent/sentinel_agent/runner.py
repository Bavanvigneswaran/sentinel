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

#: The top-processes list is the single most expensive thing in a sample: it
#: walks every process on the machine, opening each one for its name, user and
#: memory. On Windows that means a handle per process through the Tool Help
#: API and it is not cheap — running it at the 1s sample cadence is what made
#: a Windows agent unable to keep time, and a Live Monitoring trace crawl.
#:
#: It is also the slowest-moving thing in a sample, and nothing plots it: the
#: live charts draw CPU, memory, network, disk and latency. Ten seconds of
#: resolution on "what is running" loses nothing real. As with latency, the
#: value carried between probes is a genuine measurement, just an older one.
PROCESS_INTERVAL_SECONDS = 10

#: How often to repeat the "sampling is falling behind" warning. The first one
#: is logged immediately; a machine that cannot keep its budget cannot keep it
#: once, so the point is to be visible in the log without filling it.
OVERRUN_WARNING_INTERVAL_SECONDS = 60


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
        self._latest_processes: list[dict] = []
        self._last_processes_at = 0.0
        #: None while sampling keeps time; the monotonic stamp of the first of
        #: the current run of overruns otherwise. See _warn_if_overrunning.
        self._overrun_since: float | None = None
        self._last_overrun_warning = 0.0
        self._stopping = asyncio.Event()

    # --- sampling ----------------------------------------------------------

    async def sample_once(self) -> dict:
        now = time.monotonic()

        if now - self._last_latency_at >= LATENCY_INTERVAL_SECONDS:
            self._last_latency_at = now
            self._latest_latency = await latency_mod.measure_all(self._targets)

        if self.config.collect_processes and (
            now - self._last_processes_at >= PROCESS_INTERVAL_SECONDS
        ):
            self._last_processes_at = now
            # Off the event loop: process_iter() is blocking, and on a machine
            # with a few hundred processes it is long enough to stall every
            # other task in the agent — including the socket read that carries
            # the live-mode frame.
            self._latest_processes = await asyncio.to_thread(collect_processes)

        return {
            "ts": datetime.now(UTC).isoformat(),
            "system": self._system.collect(),
            "disk_usage": collect_disk_usage(),
            "disk_io": self._disk_io.collect(now),
            "net": self._net.collect(now),
            # Carried between probes so every sample has the latest known value
            # rather than gaps; the value itself is always a real measurement.
            "latency": self._latest_latency,
            "processes": self._latest_processes if self.config.collect_processes else [],
        }

    async def _sample_loop(self) -> None:
        while not self._stopping.is_set():
            started = time.monotonic()
            try:
                self.buffer.add(await self.sample_once())
            except Exception:
                logger.exception("sampling failed; continuing")
            elapsed = time.monotonic() - started
            self._warn_if_overrunning(elapsed)
            # Subtract the work already done so the cadence does not drift.
            delay = max(0.0, self.config.sample_interval_seconds - elapsed)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=delay)
            except TimeoutError:
                pass

    def _warn_if_overrunning(self, elapsed: float) -> None:
        """Say so when a sample costs more than the interval it is taken on.

        This is the one symptom that cannot be seen from the outside. An agent
        whose sample takes longer than `sample_interval_seconds` still connects,
        still handshakes and still pushes; it simply stamps its samples further
        apart than it claims, and the only place that shows up is a live chart
        that advances in jumps — which reads as a network problem, or a server
        problem, or a chart bug, and is none of them.

        It is also platform-specific by nature: `psutil.net_connections()` costs
        0.1ms on macOS because the OS *denies* it and it fails fast, and real
        milliseconds on Windows where it succeeds and walks the whole TCP table.
        A cost the development machine cannot observe is still a cost, so the
        machine that has it is the one that has to report it.

        Throttled to one line a minute after the first: an agent that is slow is
        slow on every sample, and a warning per second would bury the log it is
        trying to make readable.
        """
        budget = self.config.sample_interval_seconds
        if elapsed <= budget:
            self._overrun_since = None
            return
        now = time.monotonic()
        if self._overrun_since is None:
            self._overrun_since = now
            self._last_overrun_warning = now
        elif now - self._last_overrun_warning < OVERRUN_WARNING_INTERVAL_SECONDS:
            return
        else:
            self._last_overrun_warning = now
        logger.warning(
            "a sample took %.0fms, longer than the %.0fms budget — this device's "
            "samples are further apart than %ss and its live charts will advance "
            "in jumps. Run `sentinel-agent sample --timing` to see which collector "
            "is responsible.",
            elapsed * 1000,
            budget * 1000,
            budget,
        )

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

    async def _wait_or_stop(self, waiter) -> bool:  # noqa: ANN001
        """Run `waiter` until it finishes or the agent is asked to stop.

        Returns True when stopping won. The idle wait below reads from the
        socket, so it cannot simply be given a timeout of "until stopped" —
        both have to be raced, and the loser cancelled, or a service stop
        would sit through the rest of a push interval before exiting.
        """
        stop = asyncio.ensure_future(self._stopping.wait())
        work = asyncio.ensure_future(waiter)
        try:
            done, _ = await asyncio.wait({stop, work}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (stop, work):
                if not task.done():
                    task.cancel()
        # Surface a transport error raised inside idle() rather than treating
        # it as a completed wait; run()'s reconnect logic is what handles it.
        if work in done:
            work.result()
        return stop in done

    async def _push_loop(self, connection) -> None:  # noqa: ANN001
        while not self._stopping.is_set():
            # Raced rather than slept: connection.idle() reads the socket while
            # it waits, so a `mode` frame arriving mid-interval cuts the wait
            # short and the next push goes out at the new cadence, in the new
            # mode. Sleeping blindly here cost Live Monitoring up to a full
            # push interval of stillness before the trace started moving —
            # see AgentConnection.idle().
            if await self._wait_or_stop(connection.idle(connection.push_interval_seconds)):
                return

            raw = self.buffer.peek()
            if not raw:
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
