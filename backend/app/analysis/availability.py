"""Availability and reliability math: pure arithmetic, no I/O.

**Uptime is measured, not inferred from status.** `Device.status` is a
present-tense field (see schemas/devices.py's `derive_device_status()`) with
no history — there is nowhere to read "was this device online at 3pm last
Tuesday" from directly. What *is* real and historical is whether the agent
actually reported: `compute_uptime()` sums the SYSTEM rollup's own
`sample_weight` (the real seconds of the period the agent was actually
pushing samples for) and expresses it as a fraction of the period. This is
exactly the "reachable_ratio" idea rollups.py's LATENCY domain already
applies to a ping target, generalised to the device's own reporting record —
never a synthesised guess at what happened in a gap.

**Reliability comes from the incident/alert history that already exists.**
No new bookkeeping: `compute_reliability()` just summarises the Incident and
AlertEvent rows `app/services/report_service.py` already has to query for
the period.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AvailabilityResult:
    #: None only when the period itself has zero width (a caller error, not
    #: a real report request) — otherwise always a number, 0 for a device
    #: that never reported a single sample in the period.
    uptime_percent: float | None
    #: Real seconds of the period the device was actually reporting.
    reporting_seconds: float
    period_seconds: float


def compute_uptime(sample_weight_seconds: float, period_seconds: float) -> AvailabilityResult:
    """`sample_weight_seconds` is the SYSTEM rollup row's own `sample_weight`
    for the period (0 if the device reported nothing at all — never None,
    since a rollup bucket with zero contributing rows simply isn't returned).

    Clamped to 100%: a period whose boundaries don't land on a bucket edge
    can have its rollup source report very slightly more covered time than
    the requested window actually spans, and "104% uptime" is not a
    trustworthy-looking number even when it is only a rounding artifact of
    bucket alignment.
    """
    if period_seconds <= 0:
        return AvailabilityResult(uptime_percent=None, reporting_seconds=0.0, period_seconds=0.0)
    pct = min(100.0, 100.0 * sample_weight_seconds / period_seconds)
    return AvailabilityResult(
        uptime_percent=pct, reporting_seconds=sample_weight_seconds, period_seconds=period_seconds
    )


@dataclass(frozen=True)
class ReliabilityResult:
    incident_count: int
    resolved_incident_count: int
    #: None when no incident in the period has resolved yet — an average
    #: over zero resolved incidents is not zero seconds, it is unknown.
    mean_time_to_resolve_seconds: float | None
    alert_fired_count: int


def compute_reliability(
    incident_windows: list[tuple[datetime, datetime | None]], alert_fired_count: int
) -> ReliabilityResult:
    """`incident_windows` is `(opened_at, closed_at)` for each incident whose
    `opened_at` falls in the report period — `closed_at` is None for one
    still open. `alert_fired_count` is the caller's own count of AlertEvent
    rows fired in the period; counting is cheap enough at the DB layer that
    passing the number in is simpler than passing every row.
    """
    resolved = [(o, c) for o, c in incident_windows if c is not None]
    mean_ttr = (
        sum((c - o).total_seconds() for o, c in resolved) / len(resolved) if resolved else None
    )
    return ReliabilityResult(
        incident_count=len(incident_windows),
        resolved_incident_count=len(resolved),
        mean_time_to_resolve_seconds=mean_ttr,
        alert_fired_count=alert_fired_count,
    )
