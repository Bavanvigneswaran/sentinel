"""app/ai/insights_service.py's refresh_incident_insights(): the caching
decision (skip the API entirely when nothing correlated has changed) is the
one piece of real logic here, verified against a FakeAIClient that records
every call it receives instead of hitting the real Anthropic API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.ai.insights_service import refresh_incident_insights
from app.models import AlertEvent, User
from app.models.incidents import Incident
from app.services import enrollment_service as svc

NOW = datetime.now(UTC)


class FakeAIClient:
    def __init__(self) -> None:
        self.summarize_calls = 0
        self.root_cause_calls = 0

    async def summarize(self, *, system: str, prompt: str) -> str:
        self.summarize_calls += 1
        return f"summary #{self.summarize_calls}"

    async def analyze_root_cause(self, *, system: str, prompt: str) -> str:
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


async def test_first_refresh_calls_both_models_and_caches_the_fingerprint(open_incident):
    session, incident = open_incident["session"], open_incident["incident"]
    client = FakeAIClient()

    changed = await refresh_incident_insights(session, incident, client, now=NOW)

    assert changed is True
    assert client.summarize_calls == 1
    assert client.root_cause_calls == 1
    assert incident.summary_text == "summary #1"
    assert incident.root_cause_text == "root cause #1"
    assert incident.summary_signal_hash is not None
    assert incident.summary_signal_hash == incident.root_cause_signal_hash
    assert incident.summary_generated_at == NOW


async def test_a_second_refresh_with_no_membership_change_makes_no_api_calls(open_incident):
    session, incident = open_incident["session"], open_incident["incident"]
    client = FakeAIClient()
    await refresh_incident_insights(session, incident, client, now=NOW)

    changed = await refresh_incident_insights(
        session, incident, client, now=NOW + timedelta(minutes=1)
    )

    assert changed is False
    assert client.summarize_calls == 1  # unchanged: cache hit, no network call
    assert client.root_cause_calls == 1
    assert incident.summary_text == "summary #1"  # untouched


async def test_a_new_correlated_event_invalidates_the_cache(open_incident):
    session, incident, first_event = (
        open_incident["session"],
        open_incident["incident"],
        open_incident["event"],
    )
    client = FakeAIClient()
    await refresh_incident_insights(session, incident, client, now=NOW)

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
        session, incident, client, now=NOW + timedelta(minutes=1)
    )

    assert changed is True
    assert client.summarize_calls == 2  # membership changed -> regenerated
    assert incident.summary_text == "summary #2"


async def test_force_regenerates_even_with_unchanged_membership(open_incident):
    session, incident = open_incident["session"], open_incident["incident"]
    client = FakeAIClient()
    await refresh_incident_insights(session, incident, client, now=NOW)

    changed = await refresh_incident_insights(
        session, incident, client, now=NOW + timedelta(minutes=1), force=True
    )

    assert changed is True
    assert client.summarize_calls == 2
    assert incident.summary_text == "summary #2"


async def test_a_failed_call_leaves_the_hash_unset_so_it_retries_next_tick(open_incident):
    session, incident = open_incident["session"], open_incident["incident"]

    class FailingClient:
        async def summarize(self, *, system: str, prompt: str) -> str:
            raise RuntimeError("boom")

        async def analyze_root_cause(self, *, system: str, prompt: str) -> str:
            raise RuntimeError("boom")

    changed = await refresh_incident_insights(session, incident, FailingClient(), now=NOW)

    assert changed is False
    assert incident.summary_text is None
    assert incident.summary_signal_hash is None
