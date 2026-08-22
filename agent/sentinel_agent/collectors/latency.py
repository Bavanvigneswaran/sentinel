"""Round-trip latency to configured targets.

TCP connect rather than ICMP: raw sockets need root on every platform, and
requiring the agent to run privileged just to ping is a bad trade. A TCP
handshake to a known-open port measures the same path and needs no privileges.

An unreachable target reports `reachable=False` with a NULL RTT — never zero,
which would read as a perfect connection on every chart.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_PROBES_PER_SAMPLE = 3
DEFAULT_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class LatencyTarget:
    host: str
    port: int = 443

    @property
    def label(self) -> str:
        return f"{self.host}:{self.port}"

    @classmethod
    def parse(cls, raw: str) -> LatencyTarget:
        """Accepts "example.com" or "example.com:8443"."""
        host, _, port = raw.rpartition(":")
        if not host:
            return cls(host=raw)
        try:
            return cls(host=host, port=int(port))
        except ValueError:
            return cls(host=raw)


async def _probe(target: LatencyTarget, timeout: float) -> float | None:
    """One TCP handshake. Returns milliseconds, or None if it did not connect."""
    started = time.perf_counter()
    writer = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(target.host, target.port), timeout=timeout
        )
        return (time.perf_counter() - started) * 1000.0
    except (TimeoutError, OSError, socket.gaierror):
        return None
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def measure(
    target: LatencyTarget,
    *,
    probes: int = DEFAULT_PROBES_PER_SAMPLE,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    results = [await _probe(target, timeout) for _ in range(probes)]
    successes = [r for r in results if r is not None]
    loss = 100.0 * (len(results) - len(successes)) / len(results)

    if not successes:
        return {
            "target": target.label,
            "reachable": False,
            "rtt_ms_avg": None,
            "rtt_ms_min": None,
            "rtt_ms_max": None,
            "packet_loss_percent": 100.0,
        }

    return {
        "target": target.label,
        "reachable": True,
        "rtt_ms_avg": sum(successes) / len(successes),
        "rtt_ms_min": min(successes),
        "rtt_ms_max": max(successes),
        "packet_loss_percent": loss,
    }


async def measure_all(targets: list[LatencyTarget], **kwargs) -> list[dict]:
    if not targets:
        return []
    return list(await asyncio.gather(*(measure(t, **kwargs) for t in targets)))
