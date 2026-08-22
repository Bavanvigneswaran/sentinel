"""GET /incidents, GET /incidents/{id}, and POST /incidents/{id}/regenerate —
tenancy (another user's incident 404s, same as every other resource), the
list/detail shapes, and the regenerate route's dependency-injected AI client.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.db import AdminSessionLocal
from app.models import AlertEvent, User
from app.models.incidents import Incident
from app.security.tokens import issue_access_token
from app.services import enrollment_service as svc

NOW = datetime.now(UTC)


def _headers(user_id) -> dict:
    token, _ = issue_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def incident_with_event(admin_session):
    user = User(email="incidents-api-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="api-box")

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

    return {"user": user, "device": device, "incident": incident}


async def test_list_incidents_returns_the_callers_own(client, incident_with_event):
    user = incident_with_event["user"]
    response = await client.get("/incidents", headers=_headers(user.id))
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "open"


async def test_list_incidents_filters_by_status(client, incident_with_event):
    user, incident = incident_with_event["user"], incident_with_event["incident"]
    async with AdminSessionLocal() as session:
        row = await session.get(Incident, incident.id)
        row.status = "resolved"
        row.closed_at = NOW
        await session.commit()

    response = await client.get("/incidents?status=open", headers=_headers(user.id))
    assert response.json() == []

    response = await client.get("/incidents?status=resolved", headers=_headers(user.id))
    assert len(response.json()) == 1


async def test_get_incident_detail_includes_the_event_timeline(client, incident_with_event):
    user, incident = incident_with_event["user"], incident_with_event["incident"]
    response = await client.get(f"/incidents/{incident.id}", headers=_headers(user.id))
    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["rule_name"] == "High CPU"
    assert body["events"][0]["incident_id"] == str(incident.id)


async def test_another_users_incident_404s(client, incident_with_event):
    incident = incident_with_event["incident"]
    stranger = User(email="incidents-api-stranger@example.com", password_hash="x")
    async with AdminSessionLocal() as session:
        session.add(stranger)
        await session.commit()

    response = await client.get(f"/incidents/{incident.id}", headers=_headers(stranger.id))
    assert response.status_code == 404


async def test_a_nonexistent_incident_404s(client, incident_with_event):
    user = incident_with_event["user"]
    response = await client.get(f"/incidents/{uuid.uuid4()}", headers=_headers(user.id))
    assert response.status_code == 404


async def test_regenerate_without_an_api_key_configured_returns_503(client, incident_with_event):
    """No ANTHROPIC_API_KEY in the test environment — the route must say so
    explicitly rather than silently returning stale or fabricated text."""
    user, incident = incident_with_event["user"], incident_with_event["incident"]
    response = await client.post(
        f"/incidents/{incident.id}/regenerate", headers=_headers(user.id)
    )
    assert response.status_code == 503


async def test_regenerate_with_a_fake_client_writes_the_summary(client, incident_with_event):
    from app.api.routes.incidents import _ai_client_dependency
    from app.main import create_app

    class FakeAIClient:
        async def summarize(self, *, system: str, prompt: str) -> str:
            return "the device is running hot"

        async def analyze_root_cause(self, *, system: str, prompt: str) -> str:
            return "likely a runaway process"

    user, incident = incident_with_event["user"], incident_with_event["incident"]

    app = create_app()
    app.dependency_overrides[_ai_client_dependency] = lambda: FakeAIClient()
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        response = await ac.post(
            f"/incidents/{incident.id}/regenerate", headers=_headers(user.id)
        )

    assert response.status_code == 200
    body = response.json()
    assert body["summary_text"] == "the device is running hot"
    assert body["root_cause_text"] == "likely a runaway process"
    assert body["summary_model"] is not None
