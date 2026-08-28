"""Silence handling around notification dispatch. The send functions
themselves are monkeypatched — app/alerts/notify.py's own channel-dispatch
logic (SMTP, web push) is exercised separately in test_notify_channels.py
style modules only as needed; here the concern is purely "does the evaluator
call notify_firing/notify_resolved at the right times, and only then".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.alerts import state_apply as state_apply_module
from app.alerts.evaluator import AlertEvaluator
from app.db import AdminSessionLocal
from app.ingest.writer import write_samples
from app.models import AlertEvent, AlertRule, AlertSilence, User
from app.schemas.protocol import Sample, SystemSample
from app.services import enrollment_service as svc

T0 = datetime.now(UTC)


@pytest.fixture
async def rule_and_device(admin_session):
    user = User(email="notify-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="notify-box")

    rule = AlertRule(
        user_id=user.id,
        device_id=device.id,
        name="High CPU",
        metric="cpu_percent",
        comparison=">",
        threshold=80.0,
        for_duration_seconds=0,
    )
    admin_session.add(rule)
    await admin_session.commit()

    async with AdminSessionLocal() as session:
        await write_samples(
            session,
            device_id=device.id,
            user_id=user.id,
            samples=[Sample(ts=T0, resolution_seconds=10, system=SystemSample(cpu_percent=95.0))],
        )
    return {"user": user, "device": device, "rule": rule}


@pytest.fixture
def notify_calls(monkeypatch):
    calls = {"firing": [], "resolved": []}

    async def fake_firing(session, user_id, event, device_name):
        calls["firing"].append((user_id, event.id, device_name))

    async def fake_resolved(session, user_id, event, device_name):
        calls["resolved"].append((user_id, event.id, device_name))

    monkeypatch.setattr(state_apply_module.notify, "notify_firing", fake_firing)
    monkeypatch.setattr(state_apply_module.notify, "notify_resolved", fake_resolved)
    return calls


async def _event_for(rule_id) -> AlertEvent:
    async with AdminSessionLocal() as session:
        return await session.scalar(sa.select(AlertEvent).where(AlertEvent.rule_id == rule_id))


async def test_an_unsilenced_firing_is_notified(rule_and_device, notify_calls):
    device = rule_and_device["device"]
    rule = rule_and_device["rule"]

    # Two ticks: OK->PENDING, then PENDING->FIRING (for_duration_seconds=0
    # still needs the second tick — see analysis/alerts.py).
    await AlertEvaluator().run_once(now=T0)
    await AlertEvaluator().run_once(now=T0 + timedelta(seconds=1))

    assert len(notify_calls["firing"]) == 1
    assert notify_calls["firing"][0][2] == device.name

    event = await _event_for(rule.id)
    assert event.notified_at is not None


async def test_a_silenced_firing_is_never_notified(rule_and_device, notify_calls):
    user = rule_and_device["user"]
    device = rule_and_device["device"]
    rule = rule_and_device["rule"]

    async with AdminSessionLocal() as session:
        session.add(
            AlertSilence(
                user_id=user.id,
                device_id=device.id,
                starts_at=T0 - timedelta(minutes=1),
                ends_at=T0 + timedelta(hours=1),
            )
        )
        await session.commit()

    await AlertEvaluator().run_once(now=T0)
    await AlertEvaluator().run_once(now=T0 + timedelta(seconds=1))

    assert notify_calls["firing"] == []
    event = await _event_for(rule.id)
    assert event.status == "firing"  # the state machine still fired...
    assert event.notified_at is None  # ...it just was never paged


async def test_a_rule_scoped_silence_does_not_silence_other_rules(rule_and_device, notify_calls):
    user = rule_and_device["user"]
    device = rule_and_device["device"]

    async with AdminSessionLocal() as session:
        other_rule = AlertRule(
            user_id=user.id,
            device_id=device.id,
            name="unrelated",
            metric="mem_percent",
            comparison=">",
            threshold=1.0,  # trivially true, but never evaluated in this test
        )
        session.add(other_rule)
        await session.commit()
        session.add(
            AlertSilence(
                user_id=user.id,
                rule_id=other_rule.id,  # silences only the unrelated rule
                starts_at=T0 - timedelta(minutes=1),
                ends_at=T0 + timedelta(hours=1),
            )
        )
        await session.commit()

    await AlertEvaluator().run_once(now=T0)
    await AlertEvaluator().run_once(now=T0 + timedelta(seconds=1))

    assert len(notify_calls["firing"]) == 1


async def test_resolving_a_silenced_alert_is_also_not_notified(rule_and_device, notify_calls):
    user = rule_and_device["user"]
    device = rule_and_device["device"]
    rule = rule_and_device["rule"]

    await AlertEvaluator().run_once(now=T0)
    await AlertEvaluator().run_once(now=T0 + timedelta(seconds=1))
    assert len(notify_calls["firing"]) == 1
    notified_at_on_fire = (await _event_for(rule.id)).notified_at

    async with AdminSessionLocal() as session:
        session.add(
            AlertSilence(
                user_id=user.id,
                device_id=device.id,
                starts_at=T0,
                ends_at=T0 + timedelta(hours=1),
            )
        )
        await session.commit()
        await write_samples(
            session,
            device_id=device.id,
            user_id=user.id,
            samples=[
                Sample(
                    ts=T0 + timedelta(seconds=2),
                    resolution_seconds=10,
                    system=SystemSample(cpu_percent=5.0),
                )
            ],
        )

    await AlertEvaluator().run_once(now=T0 + timedelta(seconds=2))

    assert notify_calls["resolved"] == []
    event = await _event_for(rule.id)
    assert event.status == "resolved"
    # notified_at is left exactly as the (unsilenced) firing set it — the
    # silenced resolve never touches it again.
    assert event.notified_at == notified_at_on_fire


# --- what a notification actually says ---------------------------------------
#
# `_body` is pure, so these go straight at it rather than through a full
# evaluator sweep. `rule_type` is the discriminator for which evidence columns
# an event carries (app/models/alerts.py) — reading comparison/threshold
# unconditionally is what made every anomaly notification read
# "mem_percent = 87.3 (None None)", while both frontends had already been
# taught to branch.


class _Event:
    """Only the columns `_body` reads. A real AlertEvent needs a device, a
    rule and a tenant to exist at all, none of which say anything about the
    text this produces."""

    def __init__(self, **fields) -> None:
        self.__dict__.update(fields)


def _anomaly_event() -> _Event:
    return _Event(
        rule_type="anomaly",
        rule_name="Memory anomaly",
        metric="mem_percent",
        value_at_fire=87.3,
        resolved_value=41.0,
        comparison=None,
        threshold=None,
        baseline_mean=42.1,
        z_score=5.4,
    )


def test_an_anomaly_notification_never_renders_a_null_comparison():
    body = state_apply_module.notify._body(_anomaly_event(), "my-mac", resolved=False)

    assert "None" not in body
    assert "42.1" in body and "5.4" in body


def test_a_resolved_anomaly_notification_never_renders_a_null_comparison():
    body = state_apply_module.notify._body(_anomaly_event(), "my-mac", resolved=True)

    assert "None" not in body
    assert "41.0" in body


def test_a_threshold_notification_still_states_its_comparison():
    event = _Event(
        rule_type="threshold",
        rule_name="CPU high",
        metric="cpu_percent",
        value_at_fire=95.0,
        resolved_value=12.0,
        comparison=">",
        threshold=90.0,
    )

    assert "> 90.0" in state_apply_module.notify._body(event, "my-mac", resolved=False)


def test_a_forecast_notification_says_it_is_a_prediction():
    event = _Event(
        rule_type="forecast",
        rule_name="Disk filling",
        metric="disk_percent",
        value_at_fire=71.0,
        resolved_value=68.0,
        comparison=">",
        threshold=90.0,
        predicted_breach_at=datetime(2026, 8, 30, 4, 15, tzinfo=UTC),
        predicted_value=91.4,
    )

    body = state_apply_module.notify._body(event, "my-mac", resolved=False)

    assert "None" not in body
    # The live value is 71%, and nothing in the text may suggest it is already
    # past the threshold it is only *forecast* to cross.
    assert "forecast to reach > 90.0" in body
    assert "2026-08-30 04:15 UTC" in body


def test_an_anomaly_event_with_no_baseline_evidence_still_reads_as_a_sentence():
    """A fired anomaly always snapshots its baseline, but the columns are
    nullable and history predating them exists — degrade to plain language
    rather than formatting a None."""
    event = _anomaly_event()
    event.baseline_mean = None
    event.z_score = None

    body = state_apply_module.notify._body(event, "my-mac", resolved=False)

    assert "None" not in body
    assert "unusual for this device" in body
