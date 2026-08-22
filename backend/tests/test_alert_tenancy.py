"""RLS across the six new alert/notification tables: two tenants' rows are
mutually invisible, mirroring test_rollup_tenancy.py's pattern for the
raw hypertables.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.models import (
    AlertEvent,
    AlertRule,
    AlertSilence,
    AlertState,
    NotificationSettings,
    User,
    WebPushSubscription,
)
from app.services import enrollment_service as svc
from tests.conftest import scoped_session_for

NOW = datetime.now(UTC)


@pytest.fixture
async def two_tenants(admin_session):
    made = []
    for label in ("a", "b"):
        user = User(email=f"alert-tenant-{label}@example.com", password_hash="x")
        admin_session.add(user)
        await admin_session.commit()
        device = await svc.register_device(admin_session, user_id=user.id, name=f"box-{label}")

        rule = AlertRule(
            user_id=user.id,
            device_id=device.id,
            name=f"rule-{label}",
            metric="cpu_percent",
            comparison=">",
            threshold=90.0,
        )
        admin_session.add(rule)
        await admin_session.commit()

        state = AlertState(user_id=user.id, rule_id=rule.id, device_id=device.id, state="firing")
        admin_session.add(state)

        event = AlertEvent(
            user_id=user.id,
            rule_id=rule.id,
            device_id=device.id,
            rule_name=rule.name,
            metric="cpu_percent",
            comparison=">",
            threshold=90.0,
            status="firing",
            value_at_fire=95.0,
            fired_at=NOW,
        )
        admin_session.add(event)

        silence = AlertSilence(
            user_id=user.id,
            device_id=device.id,
            starts_at=NOW,
            ends_at=NOW + timedelta(hours=1),
        )
        admin_session.add(silence)

        settings = NotificationSettings(user_id=user.id, email_enabled=True)
        admin_session.add(settings)

        subscription = WebPushSubscription(
            user_id=user.id, endpoint=f"https://push.example/{label}", p256dh="k", auth="a"
        )
        admin_session.add(subscription)

        await admin_session.commit()
        made.append({"user": user, "device": device, "rule": rule})
    return made


async def test_the_underlying_tables_really_do_hold_both_tenants(admin_session, two_tenants):
    """Sanity check on the fixture itself, on the unscoped (owner) session."""
    for model in (
        AlertRule,
        AlertState,
        AlertEvent,
        AlertSilence,
        NotificationSettings,
        WebPushSubscription,
    ):
        count = await admin_session.scalar(sa.select(sa.func.count()).select_from(model))
        assert count == 2, model.__tablename__


async def test_a_tenant_sees_only_its_own_rules_states_events_and_silences(two_tenants):
    tenant_a = two_tenants[0]

    async with scoped_session_for(tenant_a["user"].id) as session:
        rules = list(await session.scalars(sa.select(AlertRule)))
        states = list(await session.scalars(sa.select(AlertState)))
        events = list(await session.scalars(sa.select(AlertEvent)))
        silences = list(await session.scalars(sa.select(AlertSilence)))

    assert [r.name for r in rules] == ["rule-a"]
    assert [s.device_id for s in states] == [tenant_a["device"].id]
    assert [e.rule_name for e in events] == ["rule-a"]
    assert len(silences) == 1


async def test_a_tenant_sees_only_its_own_notification_settings_and_subscriptions(two_tenants):
    tenant_a = two_tenants[0]

    async with scoped_session_for(tenant_a["user"].id) as session:
        settings = list(await session.scalars(sa.select(NotificationSettings)))
        subs = list(await session.scalars(sa.select(WebPushSubscription)))

    assert [s.user_id for s in settings] == [tenant_a["user"].id]
    assert [s.endpoint for s in subs] == ["https://push.example/a"]


async def test_an_unscoped_app_session_sees_nothing(app_session):
    """No tenant GUC set at all — every policy predicate is NULL, default deny."""
    for model in (AlertRule, AlertState, AlertEvent, AlertSilence, NotificationSettings):
        count = await app_session.scalar(sa.select(sa.func.count()).select_from(model))
        assert count == 0, model.__tablename__
