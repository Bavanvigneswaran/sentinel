"""The sampling ring buffer and its aggregation window.

The agent samples at 1s into a bounded deque and, by default, pushes a 10s
aggregate. That keeps bandwidth and server write volume down by an order of
magnitude while preserving the 1s signal locally — when a viewer opens Live
Monitoring the server flips the agent to raw 1s push and the same buffer feeds
it without re-plumbing anything.

Aggregation rules, by metric kind:

* gauges (cpu%, memory%, load, temperature) — mean over the window
* cpu% and memory% additionally carry min and max, because a spike inside the
  window is exactly the signal Phase 5's alerting and Phase 6's anomaly
  detection need, and a mean would erase it
* rates (bytes/s, IOPS, packets/s) — mean, since the collector has already
  differentiated the counters
* per-entity series (mounts, NICs, disks) — the most recent sample wins for
  capacity, mean for rates
* processes — the most recent sample only; averaging a top-10 list across a
  window is meaningless when membership changes
"""

from __future__ import annotations

import statistics
from collections import deque
from datetime import UTC, datetime
from typing import Any

#: Only these two carry min/max. p95 is deliberately absent: over a ten-sample
#: window it is indistinguishable from the max, and it becomes meaningful in
#: the server-side rollups instead.
SPREAD_METRICS = ("cpu_percent", "mem_percent")

#: Capacity-style fields where the latest reading is the truth, not an average.
LATEST_WINS = frozenset(
    {"total_bytes", "used_bytes", "free_bytes", "percent", "filesystem", "reachable"}
)


def _mean(values: list[float]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.fmean(present) if present else None


class SampleBuffer:
    """A bounded ring of raw 1s samples awaiting push."""

    def __init__(self, maxlen: int) -> None:
        self._samples: deque[dict] = deque(maxlen=maxlen)

    def __len__(self) -> int:
        return len(self._samples)

    def add(self, sample: dict) -> None:
        # deque(maxlen=...) discards the oldest automatically: under a long
        # outage we keep the most recent hour rather than the first hour.
        self._samples.append(sample)

    def peek(self) -> list[dict]:
        return list(self._samples)

    def discard(self, count: int) -> None:
        """Drop the oldest `count` samples, once the server has acked them.

        Counted rather than cleared: samples collected while a push was in
        flight must survive, or every push would silently lose a sample.
        """
        for _ in range(min(count, len(self._samples))):
            self._samples.popleft()


def _aggregate_entities(
    windows: list[list[dict]], key: str
) -> list[dict]:
    """Collapse a per-entity series (mounts, NICs, disks, targets) across the
    window, keyed by the entity's identifier."""
    by_entity: dict[str, list[dict]] = {}
    for entries in windows:
        for entry in entries:
            by_entity.setdefault(entry[key], []).append(entry)

    aggregated: list[dict] = []
    for entity, entries in by_entity.items():
        latest = entries[-1]
        merged: dict[str, Any] = {key: entity}
        for field_name in latest:
            if field_name == key:
                continue
            if field_name in LATEST_WINS:
                merged[field_name] = latest[field_name]
                continue
            values = [e.get(field_name) for e in entries]
            numeric = [v for v in values if isinstance(v, int | float) and not isinstance(v, bool)]
            merged[field_name] = _mean(numeric) if numeric else latest[field_name]
        aggregated.append(merged)
    return aggregated


def aggregate(samples: list[dict], resolution_seconds: int) -> dict | None:
    """Collapse a window of raw samples into one aggregate sample."""
    if not samples:
        return None

    latest = samples[-1]

    system: dict[str, Any] = {}
    for field_name in latest["system"]:
        values = [s["system"].get(field_name) for s in samples]
        numeric = [v for v in values if isinstance(v, int | float) and not isinstance(v, bool)]
        if not numeric:
            # Booleans and unavailable metrics: carry the most recent reading.
            system[field_name] = latest["system"][field_name]
            continue
        mean = _mean(numeric)
        # Byte counts and counts-of-things stay integers; averaging them into a
        # float would render as "8589934592.4 bytes".
        if field_name.endswith(("_bytes", "_seconds", "_users", "_count", "_connections")):
            system[field_name] = int(mean) if mean is not None else None
        else:
            system[field_name] = mean

    for metric in SPREAD_METRICS:
        values = [s["system"].get(metric) for s in samples]
        numeric = [v for v in values if isinstance(v, int | float)]
        system[f"{metric}_min"] = min(numeric) if numeric else None
        system[f"{metric}_max"] = max(numeric) if numeric else None

    return {
        # The window's end, so a sample's timestamp is never in the future.
        "ts": latest["ts"],
        "resolution_seconds": resolution_seconds,
        "system": system,
        "disk_usage": _aggregate_entities([s["disk_usage"] for s in samples], "mount"),
        "disk_io": _aggregate_entities([s["disk_io"] for s in samples], "disk"),
        "net": _aggregate_entities([s["net"] for s in samples], "nic"),
        "latency": _aggregate_entities([s["latency"] for s in samples], "target"),
        # A top-10 list cannot be averaged across a window whose membership
        # changes; the most recent ranking is the only coherent answer.
        "processes": latest["processes"],
    }


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
