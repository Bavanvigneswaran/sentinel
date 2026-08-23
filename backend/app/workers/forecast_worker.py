"""The forecast worker: a periodic sweep that computes and stores the current
24h Holt-Winters forecast and disk/memory time-to-exhaustion projection per
(device, metric), across the same 6-metric METRICS set alert rules use.

Shape mirrors app/alerts/evaluator.py almost exactly (global periodic task,
one unscoped enumeration query, everything else through a session scoped via
scope_to_user()) — see that module's docstring for the tenancy reasoning,
which applies unchanged here.

This worker only *produces* forecast data. It never evaluates an alert rule
or touches AlertState/AlertEvent — app/alerts/evaluator.py remains the one
place any rule_type is judged (see app/alerts/forecast_eval.py), reading
whatever this worker last computed. Splitting it this way means a slow or
failing ETS fit can never delay the 15s alert sweep, and this worker's own
cadence (config.forecast_worker_interval_seconds, 120s by default) only has
to suit its own CPU cost — unlike the 15s sweep, a forecast computed a couple
of minutes late is not a correctness problem.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.forecast import (
    HORIZON_SECONDS,
    ExhaustionEstimate,
    ForecastPoint,
    estimate_time_to_exhaustion,
    fit_holt_winters,
)
from app.config import get_settings
from app.db import AdminSessionLocal, SessionLocal, scope_to_user
from app.models import Device, ExhaustionForecast, MetricForecast
from app.models.alerts import METRICS
from app.models.forecasts import EXHAUSTION_METRICS
from app.models.rollups import DISK_USAGE, LATENCY, SYSTEM
from app.services.metrics_read import (
    FRESH_WINDOW_SECONDS,
    latest_per_entity,
    worst_entity_per_device,
)
from app.services.series_service import SeriesPlan, fetch_series, plan_series

logger = logging.getLogger(__name__)

#: One column per METRICS entry that lives directly on the SYSTEM domain —
#: everything except the two multi-entity metrics handled separately below.
_SYSTEM_COLUMNS = {"cpu_percent", "mem_percent", "swap_percent", "cpu_iowait_percent"}

#: Aim for hourly buckets over the lookback window: analysis/forecast.py's
#: SEASONAL_PERIOD assumes a daily cycle expressed as 24 roughly-hourly
#: buckets, and series_service.plan_series picks the coarsest source dense
#: enough for the requested point budget — 24 points/day hits that exactly.
_POINTS_PER_DAY = 24

#: A window narrower than this cannot produce enough buckets at any tier to
#: fit anything, and plan_series rejects a zero-width range outright.
_MIN_WINDOW_SECONDS = 120


def _window_start_for(device, lookback_start: datetime, now: datetime) -> datetime:
    """Where this device's forecast window should begin.

    `enrolled_at` is a lower bound on its history that is already loaded — no
    extra query — and using it is what lets a young device be forecast at all.

    Falls back to the full lookback for a device too new to narrow to, rather
    than skipping it: Phase 7's invariant is that the worker still upserts a
    row with `points=[]` so `computed_at` can say when it last checked. An
    early return here would have made the row simply not exist, which reads as
    "the worker has never run" instead of "there is nothing to fit yet".
    """
    first_seen = device.enrolled_at or device.created_at
    if first_seen is None:
        return lookback_start
    start = max(lookback_start, first_seen)
    if (now - start).total_seconds() < _MIN_WINDOW_SECONDS:
        return lookback_start
    return start


class ForecastWorker:
    def __init__(self) -> None:
        settings = get_settings()
        self._interval_seconds = settings.forecast_worker_interval_seconds
        self._history_days = settings.forecast_history_days

    async def run(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("forecast worker tick failed")
            await asyncio.sleep(self._interval_seconds)

    async def run_once(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        for user_id in await self._device_owner_user_ids():
            try:
                await self._compute_user(user_id, now)
            except Exception:
                # One tenant's bad history or a transient failure must not
                # stop the sweep from reaching everyone else.
                logger.exception("forecast computation failed user_id=%s", user_id)

    async def _device_owner_user_ids(self) -> list[uuid.UUID]:
        """The one unscoped query in this module — see the module docstring."""
        async with AdminSessionLocal() as session:
            rows = await session.scalars(
                sa.select(Device.user_id).where(Device.deleted_at.is_(None)).distinct()
            )
            return list(rows)

    async def _compute_user(self, user_id: uuid.UUID, now: datetime) -> None:
        async with SessionLocal() as session:
            scope_to_user(session, user_id)

            devices = list(
                await session.scalars(sa.select(Device).where(Device.deleted_at.is_(None)))
            )
            if not devices:
                return
            device_ids = [d.id for d in devices]

            # Which mount/target currently represents "the device's" figure
            # for a multi-entity metric — the same resolution the alert
            # evaluator uses (app/services/metrics_read.py), so the forecast
            # is always fit against one real, continuous series and never a
            # synthetic max-across-entities one.
            since = now - timedelta(seconds=FRESH_WINDOW_SECONDS)
            disk_rows = await latest_per_entity(
                session,
                table="disk_usage_samples",
                entity_keys=("mount",),
                columns=("percent",),
                since=since,
                device_ids=device_ids,
            )
            worst_mount = worst_entity_per_device(
                disk_rows, entity_key="mount", value_key="percent"
            )
            latency_rows = await latest_per_entity(
                session,
                table="latency_samples",
                entity_keys=("target",),
                columns=("packet_loss_percent",),
                since=since,
                device_ids=device_ids,
            )
            worst_target = worst_entity_per_device(
                latency_rows, entity_key="target", value_key="packet_loss_percent"
            )

            existing_forecasts = {
                (f.device_id, f.metric): f
                for f in await session.scalars(
                    sa.select(MetricForecast).where(MetricForecast.user_id == user_id)
                )
            }
            existing_exhaustion = {
                (e.device_id, e.metric): e
                for e in await session.scalars(
                    sa.select(ExhaustionForecast).where(ExhaustionForecast.user_id == user_id)
                )
            }

            lookback_start = now - timedelta(days=self._history_days)
            max_points = self._history_days * _POINTS_PER_DAY

            for device in devices:
                # Plan from when this device actually started reporting, not
                # from a flat 14 days ago. A device enrolled an hour ago has no
                # hourly buckets at all, so the flat window produced a plan at
                # 1h resolution, found ~1 row, and returned no forecast for a
                # full day — which read as "forecasting is broken" rather than
                # "not yet". plan_series already picks a finer tier for a
                # narrower window (a 20-minute span lands on 10s buckets), so
                # clamping the start is the whole fix; analysis/forecast.py's
                # MAX_HORIZON_RATIO is what keeps the resulting early forecast
                # from over-reaching.
                window_start = _window_start_for(device, lookback_start, now)
                plan = plan_series(window_start, now, max_points=max_points, now=now)
                await self._compute_device(
                    session,
                    user_id,
                    device.id,
                    plan,
                    worst_mount.get(device.id),
                    worst_target.get(device.id),
                    now,
                    existing_forecasts,
                    existing_exhaustion,
                )

            await session.commit()

    async def _compute_device(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        device_id: uuid.UUID,
        plan: SeriesPlan,
        worst_mount: tuple[str, float] | None,
        worst_target: tuple[str, float] | None,
        now: datetime,
        existing_forecasts: dict[tuple[uuid.UUID, str], MetricForecast],
        existing_exhaustion: dict[tuple[uuid.UUID, str], ExhaustionForecast],
    ) -> None:
        system_rows, _ = await fetch_series(session, device_id=device_id, domain=SYSTEM, plan=plan)

        series_by_metric: dict[str, list[tuple[datetime, float]]] = {
            column: _extract(system_rows, column) for column in _SYSTEM_COLUMNS
        }
        entity_by_metric: dict[str, str | None] = dict.fromkeys(_SYSTEM_COLUMNS)

        if worst_mount is not None:
            mount, _value = worst_mount
            disk_rows, _ = await fetch_series(
                session, device_id=device_id, domain=DISK_USAGE, plan=plan
            )
            series_by_metric["disk_percent"] = _extract_entity(
                disk_rows, "mount", mount, "percent"
            )
            entity_by_metric["disk_percent"] = mount

        if worst_target is not None:
            target, _value = worst_target
            latency_rows, _ = await fetch_series(
                session, device_id=device_id, domain=LATENCY, plan=plan
            )
            series_by_metric["packet_loss_percent"] = _extract_entity(
                latency_rows, "target", target, "packet_loss_percent"
            )
            entity_by_metric["packet_loss_percent"] = target

        for metric in METRICS:
            series = series_by_metric.get(metric, [])
            await self._compute_metric(
                session,
                user_id,
                device_id,
                metric,
                entity_by_metric.get(metric),
                series,
                plan.bucket_seconds,
                now,
                existing_forecasts,
                existing_exhaustion,
            )

    async def _compute_metric(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        device_id: uuid.UUID,
        metric: str,
        entity: str | None,
        series: list[tuple[datetime, float]],
        bucket_seconds: int,
        now: datetime,
        existing_forecasts: dict[tuple[uuid.UUID, str], MetricForecast],
        existing_exhaustion: dict[tuple[uuid.UUID, str], ExhaustionForecast],
    ) -> None:
        values = [v for _, v in series]

        try:
            points = await asyncio.to_thread(fit_holt_winters, values, bucket_seconds)
        except Exception:
            logger.exception(
                "holt-winters fit failed device_id=%s metric=%s", device_id, metric
            )
            points = None
        _upsert_forecast(
            session,
            user_id,
            device_id,
            metric,
            entity,
            points,
            bucket_seconds,
            # The real span behind the fit, which is what decides whether the
            # UI presents it as provisional. Derived from the observations
            # themselves, not from the requested window: gaps are dropped
            # upstream, so asking the window would overstate it.
            len(values) * bucket_seconds,
            now,
            existing_forecasts,
        )

        if metric not in EXHAUSTION_METRICS:
            return
        try:
            timestamps = [t for t, _ in series]
            estimate = await asyncio.to_thread(
                estimate_time_to_exhaustion, timestamps, values, now=now
            )
        except Exception:
            logger.exception(
                "exhaustion estimate failed device_id=%s metric=%s", device_id, metric
            )
            estimate = None
        if estimate is not None:
            # current_value/slope_per_day are NOT NULL — only upsert when
            # there was enough real history for a genuine estimate. Unlike
            # MetricForecast.points, there is no "empty but present" shape
            # for this row that wouldn't misrepresent an unknown trend as a
            # flat one.
            _upsert_exhaustion(
                session,
                user_id,
                device_id,
                metric,
                entity,
                values[-1],
                estimate,
                now,
                existing_exhaustion,
            )


def _extract(rows: list[dict], column: str) -> list[tuple[datetime, float]]:
    return [(row["ts"], row[column]) for row in rows if row[column] is not None]


def _extract_entity(
    rows: list[dict], entity_key: str, entity_value: str, column: str
) -> list[tuple[datetime, float]]:
    return [
        (row["ts"], row[column])
        for row in rows
        if row[entity_key] == entity_value and row[column] is not None
    ]


def _upsert_forecast(
    session: AsyncSession,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    metric: str,
    entity: str | None,
    points: tuple[ForecastPoint, ...] | None,
    bucket_seconds: int,
    history_seconds: int,
    now: datetime,
    existing: dict[tuple[uuid.UUID, str], MetricForecast],
) -> None:
    points_data = [asdict(p) for p in points] if points else []
    horizon = bucket_seconds * len(points) if points else HORIZON_SECONDS
    row = existing.get((device_id, metric))
    if row is None:
        row = MetricForecast(
            user_id=user_id,
            device_id=device_id,
            metric=metric,
            entity=entity,
            computed_at=now,
            horizon_seconds=horizon,
            bucket_seconds=bucket_seconds,
            history_seconds=history_seconds,
            points=points_data,
        )
        session.add(row)
        existing[(device_id, metric)] = row
    else:
        row.entity = entity
        row.computed_at = now
        row.horizon_seconds = horizon
        row.bucket_seconds = bucket_seconds
        row.history_seconds = history_seconds
        row.points = points_data


def _upsert_exhaustion(
    session: AsyncSession,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    metric: str,
    entity: str | None,
    current_value: float,
    estimate: ExhaustionEstimate,
    now: datetime,
    existing: dict[tuple[uuid.UUID, str], ExhaustionForecast],
) -> None:
    row = existing.get((device_id, metric))
    if row is None:
        row = ExhaustionForecast(
            user_id=user_id,
            device_id=device_id,
            metric=metric,
            entity=entity,
            computed_at=now,
            current_value=current_value,
            slope_per_day=estimate.slope_per_day,
            projected_at=estimate.projected_at,
        )
        session.add(row)
        existing[(device_id, metric)] = row
    else:
        row.entity = entity
        row.computed_at = now
        row.current_value = current_value
        row.slope_per_day = estimate.slope_per_day
        row.projected_at = estimate.projected_at
