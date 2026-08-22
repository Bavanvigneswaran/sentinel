"""ForecastWorker.run_once() against real seeded history, and the
rule_type="forecast" path through AlertEvaluator — same style as
test_anomaly_evaluator.py, since neither background loop ever runs in the
test suite (see app/alerts/evaluator.py's and
app/workers/forecast_worker.py's module docstrings).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.alerts.evaluator import AlertEvaluator
from app.db import AdminSessionLocal
from app.ingest.writer import write_samples
from app.models import AlertEvent, AlertRule, AlertState, ExhaustionForecast, MetricForecast, User
from app.schemas.protocol import DiskUsageEntry, Sample, SystemSample
from app.services import enrollment_service as svc
from app.workers.forecast_worker import ForecastWorker

T0 = datetime.now(UTC)
N_HOURS = 30
CPU_START, CPU_STEP = 30.0, 1.5
DISK_START, DISK_STEP = 40.0, 1.0


async def _seed_history(device_id, user_id) -> datetime:
    """N_HOURS of hourly samples with a clear rising cpu and disk trend.
    Returns the timestamp of the last one."""
    ts = T0
    for i in range(N_HOURS):
        ts = T0 + timedelta(hours=i)
        async with AdminSessionLocal() as session:
            await write_samples(
                session,
                device_id=device_id,
                user_id=user_id,
                samples=[
                    Sample(
                        ts=ts,
                        resolution_seconds=10,
                        system=SystemSample(cpu_percent=CPU_START + CPU_STEP * i),
                        disk_usage=[
                            DiskUsageEntry(mount="/", percent=DISK_START + DISK_STEP * i)
                        ],
                    )
                ],
                now=ts,
            )
    return ts


@pytest.fixture
async def device_with_history(admin_session):
    user = User(email="forecast-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="forecast-box")
    last_ts = await _seed_history(device.id, user.id)
    return {"user": user, "device": device, "last_ts": last_ts}


async def _forecast_for(device_id, metric) -> MetricForecast | None:
    async with AdminSessionLocal() as session:
        return await session.scalar(
            sa.select(MetricForecast).where(
                MetricForecast.device_id == device_id, MetricForecast.metric == metric
            )
        )


async def _exhaustion_for(device_id, metric) -> ExhaustionForecast | None:
    async with AdminSessionLocal() as session:
        return await session.scalar(
            sa.select(ExhaustionForecast).where(
                ExhaustionForecast.device_id == device_id, ExhaustionForecast.metric == metric
            )
        )


async def test_run_once_writes_a_cpu_forecast_with_increasing_predictions(device_with_history):
    device, last_ts = device_with_history["device"], device_with_history["last_ts"]
    await ForecastWorker().run_once(now=last_ts + timedelta(seconds=30))

    forecast = await _forecast_for(device.id, "cpu_percent")
    assert forecast is not None
    assert forecast.points  # enough real history (30 >= MIN_POINTS_TREND) to fit one
    offsets = [p["offset_seconds"] for p in forecast.points]
    assert offsets == sorted(offsets)
    for p in forecast.points:
        assert p["lower"] <= p["predicted"] <= p["upper"]

    last_observed = CPU_START + CPU_STEP * (N_HOURS - 1)
    # A clear, sustained rise should keep extrapolating upward for at least
    # the near-term part of the horizon.
    assert forecast.points[0]["predicted"] > last_observed - 1.0


async def test_run_once_writes_a_disk_exhaustion_projection(device_with_history):
    device, last_ts = device_with_history["device"], device_with_history["last_ts"]
    await ForecastWorker().run_once(now=last_ts + timedelta(seconds=30))

    exhaustion = await _exhaustion_for(device.id, "disk_percent")
    assert exhaustion is not None
    assert exhaustion.slope_per_day > 0
    assert exhaustion.projected_at is not None
    assert exhaustion.projected_at > last_ts


async def test_a_device_with_no_history_gets_no_forecast(admin_session):
    user = User(email="empty-forecast-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="empty-box")

    await ForecastWorker().run_once(now=T0)

    forecast = await _forecast_for(device.id, "cpu_percent")
    # The row exists (computed_at says "checked, found nothing") but carries
    # no points — never synthesized.
    assert forecast is not None
    assert forecast.points == []
    assert await _exhaustion_for(device.id, "disk_percent") is None


async def _state_for(rule_id, device_id) -> AlertState | None:
    async with AdminSessionLocal() as session:
        return await session.scalar(
            sa.select(AlertState).where(
                AlertState.rule_id == rule_id, AlertState.device_id == device_id
            )
        )


async def _events_for(rule_id) -> list[AlertEvent]:
    async with AdminSessionLocal() as session:
        return list(
            await session.scalars(
                sa.select(AlertEvent).where(AlertEvent.rule_id == rule_id).order_by(
                    AlertEvent.fired_at
                )
            )
        )


async def test_full_lifecycle_forecast_rule_fires_on_predicted_breach_not_live_value(
    device_with_history,
):
    user, device, last_ts = (
        device_with_history["user"],
        device_with_history["device"],
        device_with_history["last_ts"],
    )
    await ForecastWorker().run_once(now=last_ts + timedelta(seconds=30))
    forecast = await _forecast_for(device.id, "cpu_percent")
    assert forecast.points

    last_observed = CPU_START + CPU_STEP * (N_HOURS - 1)
    max_predicted = max(p["predicted"] for p in forecast.points)
    assert max_predicted > last_observed  # otherwise the test premise is void
    # Between the last real reading and the forecast's peak: guaranteed to be
    # breached by the forecast, not by a live reading that stays near
    # last_observed.
    threshold = (last_observed + max_predicted) / 2

    async with AdminSessionLocal() as session:
        rule = AlertRule(
            user_id=user.id,
            device_id=device.id,
            name="Predicted CPU breach",
            rule_type="forecast",
            metric="cpu_percent",
            comparison=">",
            threshold=threshold,
            for_duration_seconds=0,
        )
        session.add(rule)
        await session.commit()
        await session.refresh(rule)

    evaluator = AlertEvaluator()

    # First tick: a fresh live reading near last_observed (well below
    # threshold) keeps the freshness gate satisfied; the forecast alone
    # drives condition_met. Never fires on this same tick (state machine
    # guarantee), even with for_duration_seconds=0.
    t1 = last_ts + timedelta(minutes=10)
    async with AdminSessionLocal() as session:
        await write_samples(
            session,
            device_id=device.id,
            user_id=user.id,
            samples=[
                Sample(ts=t1, resolution_seconds=10, system=SystemSample(cpu_percent=last_observed))
            ],
            now=t1,
        )
    await evaluator.run_once(now=t1)
    state = await _state_for(rule.id, device.id)
    assert state.state == "pending"
    assert await _events_for(rule.id) == []

    # Second tick: forecast is unchanged (the worker hasn't re-run), live
    # reading still fresh and still below threshold -> fires on the
    # *prediction*, not the live value.
    t2 = t1 + timedelta(seconds=30)
    async with AdminSessionLocal() as session:
        await write_samples(
            session,
            device_id=device.id,
            user_id=user.id,
            samples=[
                Sample(ts=t2, resolution_seconds=10, system=SystemSample(cpu_percent=last_observed))
            ],
            now=t2,
        )
    await evaluator.run_once(now=t2)
    state = await _state_for(rule.id, device.id)
    assert state.state == "firing"
    events = await _events_for(rule.id)
    assert len(events) == 1
    event = events[0]
    assert event.rule_type == "forecast"
    assert event.value_at_fire == last_observed  # the live reading, not the prediction
    assert event.predicted_value is not None
    assert event.predicted_value > threshold
    assert event.predicted_breach_at is not None
    assert event.predicted_breach_at > forecast.computed_at
