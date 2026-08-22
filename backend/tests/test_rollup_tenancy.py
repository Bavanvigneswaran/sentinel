"""Tenancy across the continuous aggregates.

The raw hypertables are protected by RLS. The rollups cannot be: TimescaleDB
refuses to create a continuous aggregate on a hypertable with row security, and
a continuous aggregate does not accept `security_invoker`, so the view runs with
its owner's privileges and an unwrapped rollup hands every tenant's rows to
anyone who can read it.

Migration 0005's answer is a `security_barrier` wrapper view carrying an
unconditional `user_id = app_current_user_id()`, with the aggregate itself
revoked from the app role. These tests are the reason to trust that answer, and
the reason a future migration cannot quietly grant the aggregate directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.db import AdminSessionLocal, SessionLocal
from app.ingest.writer import write_samples
from app.models import User
from app.models.rollups import DOMAINS, TIERS
from app.schemas.protocol import Sample, SystemSample
from app.services import enrollment_service as svc
from tests.conftest import scoped_session_for

BASE = (datetime.now(UTC) - timedelta(minutes=10)).replace(second=0, microsecond=0)


@pytest.fixture
async def two_tenants(admin_session):
    made = []
    for label, cpu in (("a", 11.0), ("b", 99.0)):
        user = User(email=f"rollup-tenant-{label}@example.com", password_hash="x")
        admin_session.add(user)
        await admin_session.commit()
        device = await svc.register_device(
            admin_session, user_id=user.id, name=f"box-{label}"
        )
        async with AdminSessionLocal() as session:
            await write_samples(
                session,
                device_id=device.id,
                user_id=user.id,
                samples=[
                    Sample(
                        ts=BASE + timedelta(seconds=5),
                        resolution_seconds=10,
                        system=SystemSample(cpu_percent=cpu),
                    )
                ],
            )
        made.append({"user": user, "device": device, "cpu": cpu})
    return made


async def test_the_underlying_aggregate_really_does_hold_both_tenants(
    two_tenants, admin_session
):
    """Read as the owner, the aggregate is exactly as leaky as described — both
    tenants in one relation. This is what the wrapper view stands in front of,
    and asserting it here stops the test below from passing merely because the
    fixture only ever wrote one tenant's rows."""
    users = set(
        (
            await admin_session.execute(
                sa.text("SELECT DISTINCT user_id FROM cagg_metric_samples_1m")
            )
        )
        .scalars()
        .all()
    )

    # Superset, not equality: TRUNCATE on a hypertable does not invalidate an
    # aggregate that has already materialised rows from it, so any earlier test
    # that refreshed leaves its buckets behind. Irrelevant to the point here —
    # and if anything it makes the aggregate leakier than this test needs.
    assert {t["user"].id for t in two_tenants} <= users


async def test_a_rollup_shows_only_the_scoped_tenants_rows(two_tenants):
    for tenant in two_tenants:
        async with scoped_session_for(tenant["user"].id) as session:
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT DISTINCT user_id, cpu_percent FROM metric_samples_1m"
                    )
                )
            ).mappings().all()

        assert [r["user_id"] for r in rows] == [tenant["user"].id]
        assert rows[0]["cpu_percent"] == pytest.approx(tenant["cpu"])


async def test_an_unscoped_session_sees_nothing(two_tenants):
    """Same default-deny as an RLS policy: app_current_user_id() is NULL when
    the GUC was never set, and `user_id = NULL` matches no row."""
    async with SessionLocal() as session:
        count = await session.scalar(sa.text("SELECT count(*) FROM metric_samples_1m"))
    assert count == 0


async def test_the_app_role_cannot_read_an_aggregate_directly(two_tenants):
    """The unwrapped aggregate is where the cross-tenant rows live. If a future
    migration grants it, this fails."""
    async with SessionLocal() as session:
        with pytest.raises(sa.exc.ProgrammingError, match="permission denied"):
            await session.execute(sa.text("SELECT count(*) FROM cagg_metric_samples_1m"))


async def test_no_rollup_aggregate_is_granted_to_the_app_role(settings, admin_session):
    """Belt and braces over every aggregate, not just the one above.

    Migration 0001 sets ALTER DEFAULT PRIVILEGES granting the app role SELECT on
    every table and view the owner creates, so each aggregate arrives granted and
    has to be revoked. A new domain or tier added without that REVOKE is a
    cross-tenant read, and this is what catches it.
    """
    granted = (
        await admin_session.execute(
            sa.text(
                "SELECT table_name FROM information_schema.role_table_grants "
                "WHERE grantee = :role AND table_name LIKE 'cagg\\_%'"
            ),
            {"role": settings.app_db_role},
        )
    ).scalars().all()
    assert granted == []


async def test_every_domain_and_tier_has_a_scoped_view(settings, admin_session):
    """The wrapper view is the only object the application may name. If a tier
    exists without one, the query builder would resolve to a missing relation —
    or worse, someone would "fix" it by pointing at the aggregate."""
    expected = {domain.view_name(tier) for domain in DOMAINS for tier in TIERS}

    present = set(
        (
            await admin_session.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.role_table_grants "
                    "WHERE grantee = :role AND privilege_type = 'SELECT' "
                    "AND table_name = ANY(:names)"
                ),
                {"role": settings.app_db_role, "names": sorted(expected)},
            )
        )
        .scalars()
        .all()
    )
    assert present == expected


async def test_the_scoped_views_are_security_barriers(admin_session):
    """Without security_barrier a cheap user-supplied operator can be pushed
    below the tenancy predicate and see the rows it was meant to hide."""
    names = sorted({domain.view_name(tier) for domain in DOMAINS for tier in TIERS})
    unguarded = (
        await admin_session.execute(
            sa.text(
                "SELECT c.relname FROM pg_class c "
                "WHERE c.relname = ANY(:names) "
                "AND NOT COALESCE(c.reloptions::text LIKE '%security_barrier=true%', false)"
            ),
            {"names": names},
        )
    ).scalars().all()
    assert unguarded == []
