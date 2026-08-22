"""AlertEvaluator.run_once() against seeded metric_samples rows, driven across
synthetic ticks with a manually-advanced `now` — the evaluator's background
loop never runs in the test suite (see the module docstring in
app/alerts/evaluator.py), so every evaluator test calls run_once() directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.alerts.evaluator import AlertEvaluator
from app.db import AdminSessionLocal
from app.ingest.writer import write_samples
from app.models import AlertEvent, AlertRule, AlertState, Device, User
from app.schemas.protocol import Sample, SystemSample
from app.services import enrollment_service as svc

T0 = datetime.now(UTC)


async def _write_cpu_sample(device_id, user_id, *, ts: datetime, cpu_percent: float) -> None:
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
        )


@pytest.fixture
async def rule_and_device(admin_session):
    user = User(email="evaluator-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="evaluated-box")

    rule = AlertRule(
        user_id=user.id,
        device_id=device.id,
        name="High CPU",
        metric="cpu_percent",
        comparison=">",
        threshold=80.0,
        for_duration_seconds=30,
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


async def _events_for(rule_id) -> list[AlertEvent]:
    async with AdminSessionLocal() as session:
        return list(
            await session.scalars(
                sa.select(AlertEvent).where(AlertEvent.rule_id == rule_id).order_by(
                    AlertEvent.fired_at
                )
            )
        )


async def test_full_lifecycle_ok_pending_firing_resolved(rule_and_device):
    user = rule_and_device["user"]
    device = rule_and_device["device"]
    rule = rule_and_device["rule"]
    evaluator = AlertEvaluator()

    await _write_cpu_sample(device.id, user.id, ts=T0, cpu_percent=95.0)
    await evaluator.run_once(now=T0)
    state = await _state_for(rule.id, device.id)
    assert state.state == "pending"
    assert state.pending_since == T0
    assert await _events_for(rule.id) == []

    # Condition still met, but the 30s for_duration has not elapsed yet.
    await evaluator.run_once(now=T0 + timedelta(seconds=15))
    state = await _state_for(rule.id, device.id)
    assert state.state == "pending"

    # Elapsed: this tick fires.
    await evaluator.run_once(now=T0 + timedelta(seconds=30))
    state = await _state_for(rule.id, device.id)
    assert state.state == "firing"
    events = await _events_for(rule.id)
    assert len(events) == 1
    assert events[0].status == "firing"
    assert events[0].value_at_fire == 95.0
    assert state.current_event_id == events[0].id

    # Still firing on the next tick: dedup — no second event.
    await evaluator.run_once(now=T0 + timedelta(seconds=45))
    assert len(await _events_for(rule.id)) == 1

    # Condition clears.
    await _write_cpu_sample(device.id, user.id, ts=T0 + timedelta(seconds=50), cpu_percent=10.0)
    await evaluator.run_once(now=T0 + timedelta(seconds=50))
    state = await _state_for(rule.id, device.id)
    assert state.state == "ok"
    assert state.current_event_id is None
    events = await _events_for(rule.id)
    assert len(events) == 1
    assert events[0].status == "resolved"
    assert events[0].resolved_value == 10.0


async def test_a_device_with_no_fresh_sample_is_left_untouched(rule_and_device):
    device = rule_and_device["device"]
    rule = rule_and_device["rule"]
    evaluator = AlertEvaluator()

    # No metric_samples row exists at all for this device.
    await evaluator.run_once(now=T0)
    state = await _state_for(rule.id, device.id)
    assert state.state == "ok"
    assert state.last_value is None
    assert state.last_evaluated_at is not None


async def test_an_already_firing_alert_is_not_auto_resolved_by_stale_data(rule_and_device):
    """A device that goes quiet mid-firing keeps showing FIRING — resolving it
    would be a claim about a machine nobody is talking to."""
    user = rule_and_device["user"]
    device = rule_and_device["device"]
    rule = rule_and_device["rule"]
    evaluator = AlertEvaluator()

    await _write_cpu_sample(device.id, user.id, ts=T0, cpu_percent=95.0)
    await evaluator.run_once(now=T0)
    await evaluator.run_once(now=T0 + timedelta(seconds=30))
    state = await _state_for(rule.id, device.id)
    assert state.state == "firing"
    fired_event_id = state.current_event_id

    # Far enough past the freshness window (90s) that the sample above no
    # longer counts as current, and no new sample has arrived.
    await evaluator.run_once(now=T0 + timedelta(seconds=400))
    state = await _state_for(rule.id, device.id)
    assert state.state == "firing"
    assert state.current_event_id == fired_event_id
    events = await _events_for(rule.id)
    assert events[0].status == "firing"
    assert events[0].resolved_at is None


async def test_a_fleet_wide_rule_evaluates_every_device_independently(admin_session):
    user = User(email="fleet-rule-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    hot = await svc.register_device(admin_session, user_id=user.id, name="hot-box")
    cold = await svc.register_device(admin_session, user_id=user.id, name="cold-box")

    rule = AlertRule(
        user_id=user.id,
        device_id=None,  # applies to every device
        name="High CPU fleet-wide",
        metric="cpu_percent",
        comparison=">",
        threshold=80.0,
        for_duration_seconds=0,
    )
    admin_session.add(rule)
    await admin_session.commit()

    await _write_cpu_sample(hot.id, user.id, ts=T0, cpu_percent=95.0)
    await _write_cpu_sample(cold.id, user.id, ts=T0, cpu_percent=5.0)

    evaluator = AlertEvaluator()
    await evaluator.run_once(now=T0)
    await evaluator.run_once(now=T0 + timedelta(seconds=1))

    hot_state = await _state_for(rule.id, hot.id)
    cold_state = await _state_for(rule.id, cold.id)
    assert hot_state.state == "firing"
    assert cold_state.state == "ok"


async def test_a_disabled_rule_is_never_evaluated(rule_and_device):
    user = rule_and_device["user"]
    device = rule_and_device["device"]
    rule = rule_and_device["rule"]

    async with AdminSessionLocal() as session:
        await session.execute(
            sa.update(AlertRule).where(AlertRule.id == rule.id).values(enabled=False)
        )
        await session.commit()

    await _write_cpu_sample(device.id, user.id, ts=T0, cpu_percent=99.0)
    await AlertEvaluator().run_once(now=T0)

    assert await _state_for(rule.id, device.id) is None


async def test_a_rule_scoped_to_a_deleted_device_is_skipped(rule_and_device):
    device = rule_and_device["device"]
    rule = rule_and_device["rule"]

    async with AdminSessionLocal() as session:
        await session.execute(
            sa.update(Device).where(Device.id == device.id).values(deleted_at=sa.func.now())
        )
        await session.commit()

    # Should not raise even though the rule's device no longer exists.
    await AlertEvaluator().run_once(now=T0)
    assert await _state_for(rule.id, device.id) is None
