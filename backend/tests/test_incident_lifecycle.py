"""End-to-end incident correlation through the real alert evaluator: two
different rules firing on the same device attach to one Incident, the
incident stays open until every attached event resolves, and a later fire
after it closes opens a fresh one. Same run_once()-driven style as
test_alert_evaluator.py, since neither loop runs in the test suite.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.alerts.evaluator import AlertEvaluator
from app.db import AdminSessionLocal
from app.ingest.writer import write_samples
from app.models import AlertEvent, AlertRule, User
from app.models.incidents import Incident
from app.schemas.protocol import Sample, SystemSample
from app.services import enrollment_service as svc

T0 = datetime.now(UTC)


async def _write_sample(
    device_id, user_id, *, ts: datetime, cpu_percent: float, mem_percent: float
) -> None:
    async with AdminSessionLocal() as session:
        await write_samples(
            session,
            device_id=device_id,
            user_id=user_id,
            samples=[
                Sample(
                    ts=ts,
                    resolution_seconds=10,
                    system=SystemSample(cpu_percent=cpu_percent, mem_percent=mem_percent),
                )
            ],
        )


@pytest.fixture
async def two_rules_one_device(admin_session):
    user = User(email="incident-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="incident-box")

    cpu_rule = AlertRule(
        user_id=user.id,
        device_id=device.id,
        name="High CPU",
        metric="cpu_percent",
        comparison=">",
        threshold=80.0,
        for_duration_seconds=0,
    )
    mem_rule = AlertRule(
        user_id=user.id,
        device_id=device.id,
        name="High Memory",
        metric="mem_percent",
        comparison=">",
        threshold=80.0,
        for_duration_seconds=0,
    )
    admin_session.add_all([cpu_rule, mem_rule])
    await admin_session.commit()
    return {"user": user, "device": device, "cpu_rule": cpu_rule, "mem_rule": mem_rule}


async def _events_for_device(device_id) -> list[AlertEvent]:
    async with AdminSessionLocal() as session:
        return list(
            await session.scalars(
                sa.select(AlertEvent)
                .where(AlertEvent.device_id == device_id)
                .order_by(AlertEvent.fired_at)
            )
        )


async def _incidents_for_device(device_id) -> list[Incident]:
    async with AdminSessionLocal() as session:
        return list(
            await session.scalars(
                sa.select(Incident)
                .where(Incident.device_id == device_id)
                .order_by(Incident.opened_at)
            )
        )


async def test_two_rules_firing_together_correlate_into_one_incident(two_rules_one_device):
    user, device = two_rules_one_device["user"], two_rules_one_device["device"]
    evaluator = AlertEvaluator()

    await _write_sample(device.id, user.id, ts=T0, cpu_percent=95.0, mem_percent=95.0)
    await evaluator.run_once(now=T0)  # both -> pending, no fire yet
    await evaluator.run_once(now=T0 + timedelta(seconds=1))  # both fire this tick

    events = await _events_for_device(device.id)
    assert len(events) == 2
    assert all(e.status == "firing" for e in events)
    assert events[0].incident_id is not None
    assert events[0].incident_id == events[1].incident_id  # correlated, not two incidents

    incidents = await _incidents_for_device(device.id)
    assert len(incidents) == 1
    assert incidents[0].status == "open"


async def test_incident_stays_open_until_every_attached_event_resolves(two_rules_one_device):
    user, device = two_rules_one_device["user"], two_rules_one_device["device"]
    evaluator = AlertEvaluator()

    await _write_sample(device.id, user.id, ts=T0, cpu_percent=95.0, mem_percent=95.0)
    await evaluator.run_once(now=T0)
    await evaluator.run_once(now=T0 + timedelta(seconds=1))
    incident_id = (await _incidents_for_device(device.id))[0].id

    # CPU clears, memory stays high: incident must stay open.
    t2 = T0 + timedelta(seconds=2)
    await _write_sample(device.id, user.id, ts=t2, cpu_percent=10.0, mem_percent=95.0)
    await evaluator.run_once(now=t2)

    incidents = await _incidents_for_device(device.id)
    assert len(incidents) == 1
    assert incidents[0].id == incident_id
    assert incidents[0].status == "open"

    # Memory clears too: now every attached event has resolved.
    t3 = T0 + timedelta(seconds=3)
    await _write_sample(device.id, user.id, ts=t3, cpu_percent=10.0, mem_percent=10.0)
    await evaluator.run_once(now=t3)

    incidents = await _incidents_for_device(device.id)
    assert len(incidents) == 1
    assert incidents[0].id == incident_id
    assert incidents[0].status == "resolved"
    assert incidents[0].closed_at == t3

    events = await _events_for_device(device.id)
    assert all(e.status == "resolved" for e in events)


async def test_a_fire_after_the_incident_closes_opens_a_new_one(two_rules_one_device):
    user, device = two_rules_one_device["user"], two_rules_one_device["device"]
    evaluator = AlertEvaluator()

    # Open, fire, and fully resolve the first incident.
    await _write_sample(device.id, user.id, ts=T0, cpu_percent=95.0, mem_percent=95.0)
    await evaluator.run_once(now=T0)
    await evaluator.run_once(now=T0 + timedelta(seconds=1))
    t2 = T0 + timedelta(seconds=2)
    await _write_sample(device.id, user.id, ts=t2, cpu_percent=10.0, mem_percent=10.0)
    await evaluator.run_once(now=t2)
    first_incident = (await _incidents_for_device(device.id))[0]
    assert first_incident.status == "resolved"

    # A fresh fire later on: a new incident, not a reopening of the old one.
    t3 = T0 + timedelta(seconds=100)
    await _write_sample(device.id, user.id, ts=t3, cpu_percent=95.0, mem_percent=10.0)
    await evaluator.run_once(now=t3)
    t4 = T0 + timedelta(seconds=101)
    await _write_sample(device.id, user.id, ts=t4, cpu_percent=95.0, mem_percent=10.0)
    await evaluator.run_once(now=t4)

    incidents = await _incidents_for_device(device.id)
    assert len(incidents) == 2
    assert incidents[0].id == first_incident.id
    assert incidents[1].status == "open"
    assert incidents[1].id != first_incident.id
