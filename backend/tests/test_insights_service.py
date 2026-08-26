"""app/insights/service.py's refresh_incident_insights(): the caching
decision (skip regeneration entirely when nothing correlated has changed) is
the one piece of real logic here, verified against a FakeGenerator that
records every call it receives.

The fake outlives the API it was written for. When generation was a paid
call, counting calls proved no money was spent; now it proves
`*_generated_at` still means "when this explanation was reached" rather than
"when a sweep last ran over it" — see the service docstring.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.insights.service import refresh_incident_insights
from app.models import AlertEvent, User
from app.models.incidents import Incident
from app.services import enrollment_service as svc

NOW = datetime.now(UTC)


class FakeGenerator:
    def __init__(self) -> None:
        self.summarize_calls = 0
        self.root_cause_calls = 0

    def summarize(self, bundle, *, now) -> str:
        self.summarize_calls += 1
        return f"summary #{self.summarize_calls}"

    def explain(self, bundle, *, now) -> str:
        self.root_cause_calls += 1
        return f"root cause #{self.root_cause_calls}"


@pytest.fixture
async def open_incident(admin_session):
    user = User(email="insights-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="insights-box")

    incident = Incident(user_id=user.id, device_id=device.id, status="open", opened_at=NOW)
    admin_session.add(incident)
    await admin_session.commit()

    event = AlertEvent(
        user_id=user.id,
        device_id=device.id,
        incident_id=incident.id,
        rule_name="High CPU",
        rule_type="threshold",
        metric="cpu_percent",
        comparison=">",
        threshold=80.0,
        status="firing",
        value_at_fire=91.0,
        fired_at=NOW,
    )
    admin_session.add(event)
    await admin_session.commit()

    return {"session": admin_session, "incident": incident, "event": event}


async def test_first_refresh_generates_both_texts_and_caches_the_fingerprint(open_incident):
    session, incident = open_incident["session"], open_incident["incident"]
    generator = FakeGenerator()

    changed = await refresh_incident_insights(session, incident, generator, now=NOW)

    assert changed is True
    assert generator.summarize_calls == 1
    assert generator.root_cause_calls == 1
    assert incident.summary_text == "summary #1"
    assert incident.root_cause_text == "root cause #1"
    assert incident.summary_signal_hash is not None
    assert incident.summary_signal_hash == incident.root_cause_signal_hash
    assert incident.summary_generated_at == NOW


async def test_a_second_refresh_with_no_membership_change_regenerates_nothing(open_incident):
    session, incident = open_incident["session"], open_incident["incident"]
    generator = FakeGenerator()
    await refresh_incident_insights(session, incident, generator, now=NOW)

    changed = await refresh_incident_insights(
        session, incident, generator, now=NOW + timedelta(minutes=1)
    )

    assert changed is False
    assert generator.summarize_calls == 1  # unchanged: cache hit
    assert generator.root_cause_calls == 1
    assert incident.summary_text == "summary #1"  # untouched


async def test_a_new_correlated_event_invalidates_the_cache(open_incident):
    session, incident, first_event = (
        open_incident["session"],
        open_incident["incident"],
        open_incident["event"],
    )
    generator = FakeGenerator()
    await refresh_incident_insights(session, incident, generator, now=NOW)

    second_event = AlertEvent(
        user_id=first_event.user_id,
        device_id=first_event.device_id,
        incident_id=incident.id,
        rule_name="High Memory",
        rule_type="threshold",
        metric="mem_percent",
        comparison=">",
        threshold=80.0,
        status="firing",
        value_at_fire=88.0,
        fired_at=NOW + timedelta(seconds=1),
    )
    session.add(second_event)
    await session.commit()

    changed = await refresh_incident_insights(
        session, incident, generator, now=NOW + timedelta(minutes=1)
    )

    assert changed is True
    assert generator.summarize_calls == 2  # membership changed -> regenerated
    assert incident.summary_text == "summary #2"


async def test_force_regenerates_even_with_unchanged_membership(open_incident):
    session, incident = open_incident["session"], open_incident["incident"]
    generator = FakeGenerator()
    await refresh_incident_insights(session, incident, generator, now=NOW)

    changed = await refresh_incident_insights(
        session, incident, generator, now=NOW + timedelta(minutes=1), force=True
    )

    assert changed is True
    assert generator.summarize_calls == 2
    assert incident.summary_text == "summary #2"


async def test_a_failed_generation_leaves_the_hash_unset_so_it_retries_next_tick(open_incident):
    """Now guarding a template bug rather than a network failure, and still
    swallowed for the sweep's sake: one incident hitting a bad branch must
    not stop the remaining incidents for this tenant."""
    session, incident = open_incident["session"], open_incident["incident"]

    class FailingGenerator:
        def summarize(self, bundle, *, now) -> str:
            raise RuntimeError("boom")

        def explain(self, bundle, *, now) -> str:
            raise RuntimeError("boom")

    changed = await refresh_incident_insights(session, incident, FailingGenerator(), now=NOW)

    assert changed is False
    assert incident.summary_text is None
    assert incident.summary_signal_hash is None
