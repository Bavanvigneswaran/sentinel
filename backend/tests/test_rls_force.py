"""Which tables the owner role is, and is not, exempt from.

RLS has been enabled with a policy on every tenant table since 0002/0006, and
the app connects as a NOSUPERUSER/NOBYPASSRLS role, so those policies are real.
What they never constrained is the *owner* — and `get_unscoped_session()` hands
out a connection on exactly that role. Migration 0016 forces RLS on the nine
tables no owner-role path touches, which turns a future mistake there from a
silent cross-tenant read into a Postgres error.

These tests are the record of which nine, and why the other fourteen cannot
join them.
"""

from __future__ import annotations

import sqlalchemy as sa

FORCED = {
    "alert_events",
    "alert_states",
    "alert_silences",
    "anomaly_baselines",
    "exhaustion_forecasts",
    "fcm_tokens",
    "metric_forecasts",
    "notification_settings",
    "web_push_subscriptions",
}

#: Read or written through the owner role by necessity — signup and login before
#: an identity exists, `/enroll` before the agent has one, the ingest socket
#: which authenticates a device rather than a user, and four workers whose
#: periodic sweep has no request to derive a tenant GUC from.
NOT_FORCED = {
    "users",
    "refresh_tokens",
    "alert_rules",
    "enrollment_codes",
    "devices",
    "agent_tokens",
    "metric_samples",
    "disk_usage_samples",
    "disk_io_samples",
    "net_samples",
    "latency_samples",
    "process_samples",
    "incidents",
    "report_schedules",
}


async def _force_flags(admin_session) -> dict[str, bool]:
    rows = (
        await admin_session.execute(
            sa.text(
                """
                SELECT c.relname, c.relforcerowsecurity
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                """
            )
        )
    ).all()
    return {name: forced for name, forced in rows}


async def test_every_tenant_table_still_has_rls_enabled(admin_session):
    """The property forcing builds on. Without it the rest is decorative."""
    rows = (
        await admin_session.execute(
            sa.text(
                """
                SELECT c.relname, c.relrowsecurity,
                       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                  AND c.relname <> 'alembic_version'
                """
            )
        )
    ).all()
    for name, enabled, policies in rows:
        assert enabled, f"{name} has no row-level security"
        assert policies >= 1, f"{name} has RLS enabled but no policy"


async def test_the_nine_safe_tables_are_forced(admin_session):
    flags = await _force_flags(admin_session)
    for table in sorted(FORCED):
        assert flags.get(table) is True, f"{table} should be FORCE ROW LEVEL SECURITY"


async def test_the_owner_role_paths_are_not_forced(admin_session):
    """Forcing any of these breaks a real code path, silently in some cases.

    Asserted rather than left implicit so that adding one to migration 0016's
    list fails here with the reason, instead of at runtime in a background
    worker nobody is watching.
    """
    flags = await _force_flags(admin_session)
    for table in sorted(NOT_FORCED):
        assert flags.get(table) is False, (
            f"{table} is read or written through the owner role — forcing RLS on it "
            "breaks signup, /enroll, the ingest socket, or a worker's enumeration query"
        )


async def test_the_two_sets_cover_every_tenant_table(admin_session):
    """No table may sit outside both lists: a new one has to be classified."""
    flags = await _force_flags(admin_session)
    tenant_tables = set(flags) - {"alembic_version"}
    unclassified = tenant_tables - FORCED - NOT_FORCED
    assert not unclassified, (
        f"{sorted(unclassified)} is neither forced nor recorded as an owner-role path. "
        "Decide which, and say why in migration 0016."
    )


async def test_alembic_version_is_unreachable_by_the_app_role(admin_session, settings):
    """The one table with no RLS policy, and it does not need one.

    It holds a migration revision string and the restricted role has no grant on
    it at all — least privilege rather than a policy, which is the stronger
    answer. Asserted because "no policy" reads like a gap in an RLS audit until
    you check the grants.
    """
    grantees = (
        await admin_session.scalars(
            sa.text(
                """
                SELECT DISTINCT grantee FROM information_schema.role_table_grants
                WHERE table_schema = 'public' AND table_name = 'alembic_version'
                """
            )
        )
    ).all()
    assert settings.app_db_role not in grantees
