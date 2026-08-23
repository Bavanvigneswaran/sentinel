"""Assembles the one bundle every report surface renders from: the fleet-wide
or per-device analytics, availability and reliability picture over a trailing
period, compared against the equal-length period before it.

`app/api/routes/reports.py`'s JSON endpoint, `app/reports/pdf.py`,
`app/reports/csv_export.py`, and `app/workers/report_worker.py`'s scheduled
emails all call `build_report()` and render its `ReportBundle` differently —
exactly fleet_service's "one computation, several callers" shape (see that
module's docstring), so a PDF and the JSON the frontend charts can never
disagree about what a device's numbers were for the period.

Reuses rather than reimplements: `series_service.plan_series`/`fetch_series`
for the rollup reads (asking for a small `max_points` so the planner hands
back the *coarsest* available bucket — a report wants a handful of period
aggregates, not a plotted time series), and `metrics_read.latest_per_entity`/
`worst_entity_per_device` for "which mount/target is this device's" — the
same resolution Phase 7's forecast worker uses, so a report and a forecast
can never name a different mount as the one that matters.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.analytics import MetricTrend, PeriodStats, build_trend
from app.analysis.availability import (
    AvailabilityResult,
    ReliabilityResult,
    compute_reliability,
    compute_uptime,
)
from app.models import AlertEvent, Device, Incident
from app.models.alerts import METRICS
from app.models.rollups import DISK_USAGE, LATENCY, SYSTEM
from app.services.metrics_read import (
    FRESH_WINDOW_SECONDS,
    latest_per_entity,
    worst_entity_per_device,
)
from app.services.series_service import SeriesPlan, fetch_series, plan_series

DEFAULT_PERIOD_DAYS = 30
MIN_PERIOD_DAYS = 1
MAX_PERIOD_DAYS = 366

#: We only need a handful of period aggregates, never a plotted series — a
#: small budget is what makes plan_series hand back the coarsest bucket that
#: still covers the whole period (see series_service's own docstring).
_TREND_MAX_POINTS = 12

#: (avg column, min column, max column) in the SYSTEM rollup for each
#: single-entity METRICS entry. A None column means that domain does not
#: track a dedicated min/max for it — reported as unknown, never guessed
#: from the average.
_SYSTEM_METRIC_COLUMNS: dict[str, tuple[str, str | None, str | None]] = {
    "cpu_percent": ("cpu_percent", "cpu_percent_min", "cpu_percent_max"),
    "mem_percent": ("mem_percent", "mem_percent_min", "mem_percent_max"),
    "swap_percent": ("swap_percent", None, None),
    "cpu_iowait_percent": ("cpu_iowait_percent", None, None),
}

_METRIC_ORDER = {metric: i for i, metric in enumerate(METRICS)}


@dataclass
class DeviceAnalytics:
    device: Device
    period_start: datetime
    period_end: datetime
    availability: AvailabilityResult
    reliability: ReliabilityResult
    trends: list[MetricTrend]


@dataclass
class ReportBundle:
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    period_days: int
    devices: list[DeviceAnalytics]


def _weighted_avg(rows: list[dict[str, Any]], column: str) -> float | None:
    """Time-weighted mean across the (usually one, occasionally a handful,
    from bucket-boundary slop) rollup rows a period resolved to. Same
    arithmetic as fleet_service's own `_weighted_mean` — NULL rows
    contribute neither value nor weight, so a metric never measured in the
    period stays None rather than becoming zero."""
    total = weight = 0.0
    for row in rows:
        value = row.get(column)
        if value is None:
            continue
        w = float(row.get("sample_weight") or 0)
        total += value * w
        weight += w
    return total / weight if weight else None


def _extreme(rows: list[dict[str, Any]], column: str | None, pick: Any) -> float | None:
    if column is None:
        return None
    values = [row[column] for row in rows if row.get(column) is not None]
    return pick(values) if values else None


def _period_stats(
    rows: list[dict[str, Any]], avg_col: str, min_col: str | None, max_col: str | None
) -> PeriodStats:
    return PeriodStats(
        avg=_weighted_avg(rows, avg_col),
        min=_extreme(rows, min_col, min),
        max=_extreme(rows, max_col, max),
    )


def _rows_for_entity(
    rows: list[dict[str, Any]], entity_key: str, entity_value: str
) -> list[dict[str, Any]]:
    return [row for row in rows if row.get(entity_key) == entity_value]


async def build_report(
    session: AsyncSession,
    *,
    device_ids: list[uuid.UUID] | None = None,
    period_days: int = DEFAULT_PERIOD_DAYS,
    now: datetime | None = None,
) -> ReportBundle:
    """Every device's analytics for one period, read at one instant.

    The session must already be tenant-scoped — raw tables are RLS-covered
    and the rollups are reached only through their scoped wrapper views, so
    neither this function nor its callers filter by user_id.
    """
    now = now or datetime.now(UTC)
    period_days = max(MIN_PERIOD_DAYS, min(MAX_PERIOD_DAYS, period_days))
    period_end = now
    period_start = now - timedelta(days=period_days)
    previous_start = period_start - timedelta(days=period_days)

    query = sa.select(Device).where(Device.deleted_at.is_(None))
    if device_ids:
        query = query.where(Device.id.in_(device_ids))
    devices = list(await session.scalars(query.order_by(Device.name)))
    if not devices:
        return ReportBundle(now, period_start, period_end, period_days, [])

    ids = [d.id for d in devices]
    since = now - timedelta(seconds=FRESH_WINDOW_SECONDS)
    disk_rows = await latest_per_entity(
        session, table="disk_usage_samples", entity_keys=("mount",), columns=("percent",),
        since=since, device_ids=ids,
    )
    worst_mount = worst_entity_per_device(disk_rows, entity_key="mount", value_key="percent")
    latency_rows = await latest_per_entity(
        session, table="latency_samples", entity_keys=("target",),
        columns=("packet_loss_percent",), since=since, device_ids=ids,
    )
    worst_target = worst_entity_per_device(
        latency_rows, entity_key="target", value_key="packet_loss_percent"
    )

    current_plan = plan_series(period_start, period_end, max_points=_TREND_MAX_POINTS, now=now)
    previous_plan = plan_series(previous_start, period_start, max_points=_TREND_MAX_POINTS, now=now)

    analytics = [
        await _device_analytics(
            session,
            device,
            current_plan,
            previous_plan,
            period_start,
            period_end,
            worst_mount.get(device.id),
            worst_target.get(device.id),
        )
        for device in devices
    ]
    return ReportBundle(now, period_start, period_end, period_days, analytics)


async def _device_analytics(
    session: AsyncSession,
    device: Device,
    current_plan: SeriesPlan,
    previous_plan: SeriesPlan,
    period_start: datetime,
    period_end: datetime,
    worst_mount: tuple[str, float] | None,
    worst_target: tuple[str, float] | None,
) -> DeviceAnalytics:
    current_system, _ = await fetch_series(
        session, device_id=device.id, domain=SYSTEM, plan=current_plan
    )
    previous_system, _ = await fetch_series(
        session, device_id=device.id, domain=SYSTEM, plan=previous_plan
    )

    trends = [
        build_trend(
            metric,
            None,
            _period_stats(current_system, avg_col, min_col, max_col),
            _period_stats(previous_system, avg_col, min_col, max_col),
        )
        for metric, (avg_col, min_col, max_col) in _SYSTEM_METRIC_COLUMNS.items()
    ]

    if worst_mount is not None:
        mount, _value = worst_mount
        current_disk, _ = await fetch_series(
            session, device_id=device.id, domain=DISK_USAGE, plan=current_plan
        )
        previous_disk, _ = await fetch_series(
            session, device_id=device.id, domain=DISK_USAGE, plan=previous_plan
        )
        current_disk_stats = _period_stats(
            _rows_for_entity(current_disk, "mount", mount), "percent", None, "percent_max"
        )
        previous_disk_stats = _period_stats(
            _rows_for_entity(previous_disk, "mount", mount), "percent", None, "percent_max"
        )
        trends.append(build_trend("disk_percent", mount, current_disk_stats, previous_disk_stats))

    if worst_target is not None:
        target, _value = worst_target
        current_lat, _ = await fetch_series(
            session, device_id=device.id, domain=LATENCY, plan=current_plan
        )
        previous_lat, _ = await fetch_series(
            session, device_id=device.id, domain=LATENCY, plan=previous_plan
        )
        current_lat_stats = _period_stats(
            _rows_for_entity(current_lat, "target", target), "packet_loss_percent", None, None
        )
        previous_lat_stats = _period_stats(
            _rows_for_entity(previous_lat, "target", target), "packet_loss_percent", None, None
        )
        trends.append(
            build_trend("packet_loss_percent", target, current_lat_stats, previous_lat_stats)
        )

    trends.sort(key=lambda t: _METRIC_ORDER.get(t.metric, len(_METRIC_ORDER)))

    reporting_seconds = sum(float(row.get("sample_weight") or 0) for row in current_system)
    availability = compute_uptime(reporting_seconds, (period_end - period_start).total_seconds())

    reliability = await _reliability(session, device.id, period_start, period_end)

    return DeviceAnalytics(
        device=device,
        period_start=period_start,
        period_end=period_end,
        availability=availability,
        reliability=reliability,
        trends=trends,
    )


async def _reliability(
    session: AsyncSession, device_id: uuid.UUID, period_start: datetime, period_end: datetime
) -> ReliabilityResult:
    incident_rows = await session.execute(
        sa.select(Incident.opened_at, Incident.closed_at).where(
            Incident.device_id == device_id,
            Incident.opened_at >= period_start,
            Incident.opened_at < period_end,
        )
    )
    windows = [(row.opened_at, row.closed_at) for row in incident_rows]

    alert_fired_count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(AlertEvent)
        .where(
            AlertEvent.device_id == device_id,
            AlertEvent.fired_at >= period_start,
            AlertEvent.fired_at < period_end,
        )
    )
    return compute_reliability(windows, alert_fired_count or 0)
