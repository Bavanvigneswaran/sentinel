"""AlertEvaluator.run_once() against a rule_type="anomaly" rule, driven
across synthetic ticks — same style as test_alert_evaluator.py, since the
evaluator's background loop never runs in the test suite (see
app/alerts/evaluator.py's module docstring).

Idle samples are held at an exact constant (20.0) through warmup so the
baseline's mad is exactly 0 going into the first anomalous tick and
MIN_SPREAD is the whole spread at judgment time — this makes every expected
mean/mad/z-score value exact arithmetic from there on rather than an
approximation, which is what makes the ordering-regression assertion in
test_full_lifecycle_warmup_to_fire_to_resolve precise rather than a fuzzy
"close to".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.alerts.evaluator import AlertEvaluator
from app.analysis.anomaly import MAD_SCALE, MIN_SPREAD, WARMUP_SAMPLES, classify_severity
from app.db import AdminSessionLocal
from app.ingest.writer import write_samples
from app.models import (
    AlertEvent,
    AlertRule,
    AlertState,
    AnomalyBaseline,
    NotificationSettings,
    User,
)
from app.schemas.protocol import Sample, SystemSample
from app.services import enrollment_service as svc

T0 = datetime.now(UTC)
IDLE = 20.0
SPIKE = 95.0


async def _write_cpu_sample(device_id, user_id, *, ts: datetime, cpu_percent: float) -> None:
    # write_samples() rejects a sample whose `ts` is more than
    # MAX_SAMPLE_SKEW_SECONDS ahead of `now` (default: real wall-clock time).
    # Our synthetic `ts` values run far ahead of real time across a warmup
    # sequence, so `now` must be pinned to `ts` itself, same as
    # AlertEvaluator.run_once(now=...) already is in these tests.
    async with AdminSessionLocal() as session:
        await write_samples(
            session,
            device_id=device_id,
            user_id=user_id,
            samples=[
                Sample(
                    ts=ts, resolution_seconds=10, system=SystemSample(cpu_percent=cpu_percent)
                )
            ],
            now=ts,
        )


@pytest.fixture
async def rule_and_device(admin_session):
    user = User(email="anomaly-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="anomaly-box")

    rule = AlertRule(
        user_id=user.id,
        device_id=device.id,
        name="Unusual CPU",
        rule_type="anomaly",
        metric="cpu_percent",
        for_duration_seconds=0,
    )
    admin_session.add(rule)
    await admin_session.commit()
    return {"user": user, "device": device, "rule": rule}


async def _state_for(rule_id, device_id) -> AlertState | None:
    async with AdminSessionLocal() as session:
        return await session.scalar(
            sa.select(AlertState).where(
                AlertState.rule_id == rule_id, AlertState.device_id == device_id
            )
        )


async def _baseline_for(device_id, metric) -> AnomalyBaseline | None:
    async with AdminSessionLocal() as session:
        return await session.scalar(
            sa.select(AnomalyBaseline).where(
                AnomalyBaseline.device_id == device_id, AnomalyBaseline.metric == metric
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


async def _warm_up(evaluator, device, user, *, ticks: int = WARMUP_SAMPLES, start=T0):
    """Feed `ticks` identical idle samples, one evaluator tick apart. Returns
    the `now` of the last tick run."""
    now = start
    for i in range(ticks):
        now = start + timedelta(seconds=10 * i)
        await _write_cpu_sample(device.id, user.id, ts=now, cpu_percent=IDLE)
        await evaluator.run_once(now=now)
    return now


async def test_warmup_produces_no_state_change_and_no_event(rule_and_device):
    user, device, rule = (
        rule_and_device["user"],
        rule_and_device["device"],
        rule_and_device["rule"],
    )
    evaluator = AlertEvaluator()

    await _warm_up(evaluator, device, user, ticks=WARMUP_SAMPLES - 1)

    state = await _state_for(rule.id, device.id)
    assert state.state == "ok"
    assert await _events_for(rule.id) == []
    baseline = await _baseline_for(device.id, "cpu_percent")
    assert baseline.sample_count == WARMUP_SAMPLES - 1
    assert baseline.mean == IDLE
    assert baseline.mad == 0.0


async def test_full_lifecycle_warmup_to_fire_to_resolve(rule_and_device):
    user, device, rule = (
        rule_and_device["user"],
        rule_and_device["device"],
        rule_and_device["rule"],
    )
    evaluator = AlertEvaluator()

    last_warmup_tick = await _warm_up(evaluator, device, user)
    baseline_after_warmup = await _baseline_for(device.id, "cpu_percent")
    assert baseline_after_warmup.sample_count == WARMUP_SAMPLES
    assert baseline_after_warmup.mean == IDLE

    # First spike: judged against the untouched idle baseline (mean=20,
    # mad=0 -> spread floor 0.5), wildly anomalous -> enters PENDING. Never
    # fires on this same tick even with for_duration_seconds=0.
    t1 = last_warmup_tick + timedelta(seconds=10)
    await _write_cpu_sample(device.id, user.id, ts=t1, cpu_percent=SPIKE)
    await evaluator.run_once(now=t1)
    state = await _state_for(rule.id, device.id)
    assert state.state == "pending"
    assert await _events_for(rule.id) == []
    # The pending tick still folds its value into the baseline regardless of
    # not having fired — decision #5.
    baseline = await _baseline_for(device.id, "cpu_percent")
    mean_after_first_spike = IDLE + 0.1 * (SPIKE - IDLE)  # 27.5
    mad_after_first_spike = 0.0 + 0.1 * (abs(SPIKE - mean_after_first_spike) - 0.0)  # 6.75
    assert baseline.mean == pytest.approx(mean_after_first_spike)
    assert baseline.mad == pytest.approx(mad_after_first_spike)

    # Second spike: fires. The evidence snapshotted on the event must be the
    # baseline as it stood BEFORE *this* tick's value was folded in (mean
    # 27.5, from the one prior fold-in) — not a baseline that has already
    # absorbed this tick's own 95.0 reading too. This is the regression test
    # for decision #7's judge-before-update ordering: a buggy
    # judge-after-update implementation would instead have folded this
    # tick's spike in before scoring it, producing a different (larger)
    # baseline_mean here.
    t2 = t1 + timedelta(seconds=10)
    await _write_cpu_sample(device.id, user.id, ts=t2, cpu_percent=SPIKE)
    await evaluator.run_once(now=t2)
    state = await _state_for(rule.id, device.id)
    assert state.state == "firing"
    events = await _events_for(rule.id)
    assert len(events) == 1
    assert events[0].status == "firing"
    assert events[0].observed_value == SPIKE
    assert events[0].baseline_mean == pytest.approx(mean_after_first_spike)
    assert events[0].baseline_mad == pytest.approx(mad_after_first_spike)
    expected_spread = max(mad_after_first_spike * MAD_SCALE, MIN_SPREAD)
    assert events[0].z_score == pytest.approx((SPIKE - mean_after_first_spike) / expected_spread)
    assert events[0].comparison is None
    assert events[0].threshold is None
    # severity is a read-time computed field (AlertEventOut.severity, not a
    # stored column) — check the pure classifier against the stored z_score.
    assert classify_severity(events[0].z_score) == "critical"

    # Baseline keeps adapting while firing, tick over tick.
    sample_count_at_fire = (await _baseline_for(device.id, "cpu_percent")).sample_count
    t3 = t2 + timedelta(seconds=10)
    await _write_cpu_sample(device.id, user.id, ts=t3, cpu_percent=SPIKE)
    await evaluator.run_once(now=t3)
    assert (await _baseline_for(device.id, "cpu_percent")).sample_count == sample_count_at_fire + 1

    # Condition clears: back to idle.
    t4 = t3 + timedelta(seconds=10)
    await _write_cpu_sample(device.id, user.id, ts=t4, cpu_percent=IDLE)
    await evaluator.run_once(now=t4)
    state = await _state_for(rule.id, device.id)
    assert state.state == "ok"
    events = await _events_for(rule.id)
    assert len(events) == 1
    assert events[0].status == "resolved"
    assert events[0].resolved_value == IDLE


async def test_missing_reading_advances_neither_state_nor_baseline(rule_and_device):
    user, device, rule = (
        rule_and_device["user"],
        rule_and_device["device"],
        rule_and_device["rule"],
    )
    evaluator = AlertEvaluator()
    last_tick = await _warm_up(evaluator, device, user)
    baseline_before = await _baseline_for(device.id, "cpu_percent")

    # No new sample written; advance well past FRESH_WINDOW_SECONDS (90s) so
    # the last warmup sample no longer counts as fresh.
    stale_tick = last_tick + timedelta(seconds=200)
    await evaluator.run_once(now=stale_tick)

    state = await _state_for(rule.id, device.id)
    assert state.state == "ok"
    assert state.last_evaluated_at == stale_tick
    baseline_after = await _baseline_for(device.id, "cpu_percent")
    assert baseline_after.sample_count == baseline_before.sample_count
    assert baseline_after.mean == baseline_before.mean


async def test_sensitivity_setting_changes_the_firing_outcome(admin_session):
    user = User(email="sensitivity-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="sensitivity-box")
    rule = AlertRule(
        user_id=user.id,
        device_id=device.id,
        name="Unusual CPU",
        rule_type="anomaly",
        metric="cpu_percent",
        for_duration_seconds=0,
    )
    admin_session.add(rule)
    await admin_session.commit()

    # A borderline deviation: z = (21.5 - 20) / 0.5 = 3.0 exactly. That's
    # below "low"'s cutoff (4.0) but at/above "high"'s cutoff (2.0).
    borderline = 21.5

    async def _run_to_borderline_spike(sensitivity: str) -> str:
        async with AdminSessionLocal() as session:
            session.add(NotificationSettings(user_id=user.id, anomaly_sensitivity=sensitivity))
            await session.commit()
        evaluator = AlertEvaluator()
        last_tick = await _warm_up(evaluator, device, user)
        t1 = last_tick + timedelta(seconds=10)
        await _write_cpu_sample(device.id, user.id, ts=t1, cpu_percent=borderline)
        await evaluator.run_once(now=t1)
        t2 = t1 + timedelta(seconds=10)
        await _write_cpu_sample(device.id, user.id, ts=t2, cpu_percent=borderline)
        await evaluator.run_once(now=t2)
        state = await _state_for(rule.id, device.id)
        return state.state

    assert await _run_to_borderline_spike("low") in ("ok", "pending")

    # Reset state/baseline for a clean second run under a different setting.
    async with AdminSessionLocal() as session:
        await session.execute(sa.delete(AlertState).where(AlertState.rule_id == rule.id))
        await session.execute(
            sa.delete(AnomalyBaseline).where(AnomalyBaseline.device_id == device.id)
        )
        await session.execute(
            sa.update(NotificationSettings)
            .where(NotificationSettings.user_id == user.id)
            .values(anomaly_sensitivity="high")
        )
        await session.commit()

    evaluator = AlertEvaluator()
    last_tick = await _warm_up(evaluator, device, user, start=T0 + timedelta(hours=1))
    t1 = last_tick + timedelta(seconds=10)
    await _write_cpu_sample(device.id, user.id, ts=t1, cpu_percent=borderline)
    await evaluator.run_once(now=t1)
    t2 = t1 + timedelta(seconds=10)
    await _write_cpu_sample(device.id, user.id, ts=t2, cpu_percent=borderline)
    await evaluator.run_once(now=t2)
    assert (await _state_for(rule.id, device.id)).state == "firing"


async def test_threshold_and_anomaly_rules_on_same_device_metric_are_independent(admin_session):
    user = User(email="mixed-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="mixed-box")

    threshold_rule = AlertRule(
        user_id=user.id,
        device_id=device.id,
        name="High CPU threshold",
        rule_type="threshold",
        metric="cpu_percent",
        comparison=">",
        threshold=80.0,
        for_duration_seconds=0,
    )
    anomaly_rule = AlertRule(
        user_id=user.id,
        device_id=device.id,
        name="Unusual CPU",
        rule_type="anomaly",
        metric="cpu_percent",
        for_duration_seconds=0,
    )
    admin_session.add_all([threshold_rule, anomaly_rule])
    await admin_session.commit()

    evaluator = AlertEvaluator()
    last_tick = await _warm_up(evaluator, device, user)

    # A moderate rise: above the anomaly baseline (idle=20, spread floor
    # 0.5) but below the threshold rule's fixed 80.0 cutoff.
    t1 = last_tick + timedelta(seconds=10)
    await _write_cpu_sample(device.id, user.id, ts=t1, cpu_percent=40.0)
    await evaluator.run_once(now=t1)
    t2 = t1 + timedelta(seconds=10)
    await _write_cpu_sample(device.id, user.id, ts=t2, cpu_percent=40.0)
    await evaluator.run_once(now=t2)

    assert (await _state_for(threshold_rule.id, device.id)).state == "ok"
    assert (await _state_for(anomaly_rule.id, device.id)).state == "firing"
