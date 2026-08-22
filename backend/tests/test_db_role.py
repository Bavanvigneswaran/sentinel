"""The application must not connect as a role that can bypass RLS.

Cheap, but it is what keeps the whole row-level-security design honest: a
superuser silently ignores every policy, and every tenancy test would still
pass while the running app leaked across tenants.
"""

import sqlalchemy as sa


async def test_app_role_cannot_bypass_rls(app_session):
    row = (
        await app_session.execute(
            sa.text(
                "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                "FROM pg_roles WHERE rolname = current_user"
            )
        )
    ).one()
    assert row.rolsuper is False
    assert row.rolbypassrls is False
    assert row.rolcreatedb is False
    assert row.rolcreaterole is False


async def test_app_role_cannot_create_tables(app_session):
    with __import__("pytest").raises(Exception):
        await app_session.execute(sa.text("CREATE TABLE should_not_exist (i int)"))


async def test_every_tenant_table_has_rls_enabled(app_session):
    rows = (
        await app_session.execute(
            sa.text(
                "SELECT tablename, rowsecurity FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
    ).all()
    assert rows, "no tables found"
    disabled = [r.tablename for r in rows if not r.rowsecurity]
    assert disabled == [], f"tables without RLS: {disabled}"


async def test_every_tenant_table_has_a_policy_with_a_with_check(app_session):
    rows = (
        await app_session.execute(
            sa.text("SELECT tablename, policyname, cmd, qual, with_check FROM pg_policies "
                    "WHERE schemaname = 'public'")
        )
    ).all()
    by_table = {r.tablename: r for r in rows}
    for table in ("users", "devices", "refresh_tokens", "agent_tokens", "enrollment_codes"):
        assert table in by_table, f"{table} has no RLS policy"
        policy = by_table[table]
        assert policy.cmd == "ALL"
        assert "app_current_user_id()" in policy.qual
        # Without WITH CHECK a user could INSERT rows owned by someone else.
        assert policy.with_check and "app_current_user_id()" in policy.with_check
