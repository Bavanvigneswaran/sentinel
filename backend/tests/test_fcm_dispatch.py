"""app/alerts/notify.py's FCM leg.

The behaviour worth pinning: an FcmToken row is on its own sufficient to be
notified. The Android viewer has no email or web-push UI and so never calls
GET /notifications/settings — which is what lazily creates the
NotificationSettings row — so gating FCM on that row would mean a phone that
registered a token silently received nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.alerts import fcm as fcm_module
from app.alerts import notify
from app.db import AdminSessionLocal
from app.models import AlertEvent, AlertRule, FcmToken, NotificationSettings, User
from app.services import enrollment_service as svc

TOKEN = "fcm-dispatch-token-000000000000000000"


@pytest.fixture
async def fired_event(admin_session):
    user = User(email="fcm-dispatch@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="fcm-box")

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

    event = AlertEvent(
        user_id=user.id,
        rule_id=rule.id,
        device_id=device.id,
        rule_name=rule.name,
        rule_type="threshold",
        metric="cpu_percent",
        comparison=">",
        threshold=80.0,
        status="firing",
        value_at_fire=95.0,
        fired_at=datetime.now(UTC),
    )
    admin_session.add(event)
    await admin_session.commit()
    return {"user": user, "device": device, "event": event}


@pytest.fixture
def sent(monkeypatch):
    """Capture what would have gone to FCM, and pretend it is configured."""
    calls: list[dict] = []

    async def fake_send(tokens, *, title, body, data):
        calls.append({"tokens": list(tokens), "title": title, "body": body, "data": data})
        return []

    monkeypatch.setattr(fcm_module, "is_configured", lambda: True)
    monkeypatch.setattr(fcm_module, "send_to_tokens", fake_send)
    return calls


async def test_a_registered_token_is_notified_with_no_settings_row(fired_event, sent):
    user = fired_event["user"]
    async with AdminSessionLocal() as session:
        session.add(FcmToken(user_id=user.id, token=TOKEN))
        await session.commit()

    async with AdminSessionLocal() as session:
        assert await session.get(NotificationSettings, user.id) is None
        await notify.notify_firing(session, user.id, fired_event["event"], "fcm-box")

    assert len(sent) == 1
    assert sent[0]["tokens"] == [TOKEN]
    assert "High CPU" in sent[0]["title"]
    assert sent[0]["data"] == {"url": "/alerts"}


async def test_a_user_with_no_registered_device_sends_nothing(fired_event, sent):
    user = fired_event["user"]
    async with AdminSessionLocal() as session:
        await notify.notify_firing(session, user.id, fired_event["event"], "fcm-box")
    assert sent == []


async def test_only_the_owning_users_tokens_are_addressed(fired_event, sent, make_user):
    """The dispatch query filters on user_id even though it runs on an admin
    session; a stranger's phone must never receive another tenant's alert."""
    owner = fired_event["user"]
    stranger = await make_user(email="fcm-stranger@example.com")

    async with AdminSessionLocal() as session:
        session.add(FcmToken(user_id=owner.id, token=TOKEN))
        session.add(FcmToken(user_id=stranger.id, token="fcm-stranger-token-1111111111111111"))
        await session.commit()

    async with AdminSessionLocal() as session:
        await notify.notify_firing(session, owner.id, fired_event["event"], "fcm-box")

    assert sent[0]["tokens"] == [TOKEN]


async def test_a_resolved_alert_also_pushes(fired_event, sent):
    user = fired_event["user"]
    async with AdminSessionLocal() as session:
        session.add(FcmToken(user_id=user.id, token=TOKEN))
        await session.commit()

    async with AdminSessionLocal() as session:
        await notify.notify_resolved(session, user.id, fired_event["event"], "fcm-box")

    assert "resolved" in sent[0]["title"]


async def test_a_dead_token_reported_by_fcm_is_deleted(fired_event, monkeypatch):
    """Routine cleanup, exactly as a 404/410 from a web push endpoint is."""
    user = fired_event["user"]

    async def fake_send(tokens, *, title, body, data):
        return list(tokens)

    monkeypatch.setattr(fcm_module, "is_configured", lambda: True)
    monkeypatch.setattr(fcm_module, "send_to_tokens", fake_send)

    async with AdminSessionLocal() as session:
        session.add(FcmToken(user_id=user.id, token=TOKEN))
        await session.commit()

    async with AdminSessionLocal() as session:
        await notify.notify_firing(session, user.id, fired_event["event"], "fcm-box")
        await session.commit()

    async with AdminSessionLocal() as session:
        remaining = list(await session.scalars(sa.select(FcmToken)))
    assert remaining == []
