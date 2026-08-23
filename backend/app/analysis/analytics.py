"""Historical trend math: pure arithmetic over two already-fetched period
aggregates, no I/O. `app/services/report_service.py` is the only caller,
supplying `current`/`previous` as the single time-weighted rollup row each
period produces via `series_service.plan_series(..., max_points=1)` — see
that module's docstring for why one bucket can stand in for a whole period.

Mirrors analysis/forecast.py's and analysis/anomaly.py's shape: a dataclass
result plus a builder function, both free of the ORM/session types their
callers pass around.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PeriodStats:
    """One period's aggregate reading for one metric — exactly the shape a
    single time-weighted rollup row for that period produces. `avg` is None
    when the platform reported no samples for the metric anywhere in the
    period; `min`/`max` follow the same NULL-survives-the-rollup rule.
    """

    avg: float | None
    min: float | None
    max: float | None


@dataclass(frozen=True)
class MetricTrend:
    metric: str
    #: The mount (disk_percent) or target (packet_loss_percent) this trend
    #: was computed against — None for a single-entity metric. See
    #: services/metrics_read.py's worst_entity_per_device(), reused here.
    entity: str | None
    current: PeriodStats
    previous: PeriodStats
    #: Percent change of current.avg relative to previous.avg. None whenever
    #: either average is unavailable, or previous.avg is exactly zero (a
    #: percent change against zero is undefined, not infinite).
    delta_percent: float | None


def build_trend(
    metric: str, entity: str | None, current: PeriodStats, previous: PeriodStats
) -> MetricTrend:
    delta_percent = None
    if current.avg is not None and previous.avg is not None and previous.avg != 0:
        delta_percent = 100.0 * (current.avg - previous.avg) / abs(previous.avg)
    return MetricTrend(
        metric=metric,
        entity=entity,
        current=current,
        previous=previous,
        delta_percent=delta_percent,
    )
