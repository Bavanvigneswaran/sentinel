"""bootstrap the restricted app role and the tenant helper function

Revision ID: 0001_bootstrap_roles
Revises:
Create Date: Phase 1

Split from the schema migration on purpose: ALTER DEFAULT PRIVILEGES only
applies to objects created *after* it runs, so the grants here must be in place
before 0002 creates any table.
"""

from __future__ import annotations

from alembic import op

from app.config import ROLE_PASSWORD_RE, get_settings

revision = "0001_bootstrap_roles"
down_revision = None
branch_labels = None
depends_on = None

TENANT_GUC = "app.current_user_id"


def upgrade() -> None:
    settings = get_settings()
    role = settings.app_db_role

    if not role.isidentifier():
        raise ValueError(f"app_db_role must be a plain identifier, got {role!r}")

    if settings.manage_app_role:
        password = settings.app_db_password
        # CREATE ROLE ... PASSWORD takes a literal, not a bind parameter. The
        # value is our own config, and this pattern makes the interpolation
        # provably safe. Settings validates it too; belt and braces.
        if not ROLE_PASSWORD_RE.match(password):
            raise ValueError("app_db_password must match ^[A-Za-z0-9_-]{8,64}$")

        # Roles are cluster-global, so this is a no-op when the test database is
        # migrated on the same container. The per-database grants below still apply.
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    CREATE ROLE {role} LOGIN PASSWORD '{password}'
                        NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                END IF;
            END
            $$;
            """
        )

    # NULLIF guards the one real footgun: current_setting(..., true) returns an
    # empty string (not NULL) once the GUC has been set and cleared, and
    # ''::uuid raises rather than yielding NULL — which would turn every query
    # into a 500 instead of an empty result.
    #
    # STABLE, not IMMUTABLE: STABLE lets the planner treat
    # `user_id = app_current_user_id()` as an indexable predicate, while
    # IMMUTABLE would be a lie and risks constant-folding across statements.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION app_current_user_id() RETURNS uuid
        LANGUAGE sql
        STABLE
        SET search_path = pg_catalog
        AS $$ SELECT NULLIF(current_setting('{TENANT_GUC}', true), '')::uuid $$;
        """
    )

    op.execute(f"GRANT EXECUTE ON FUNCTION app_current_user_id() TO {role};")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role};")

    # Applies to tables created from 0002 onwards. The app role gets DML only —
    # never DDL, never ownership.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role};"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {role};"
    )


def downgrade() -> None:
    settings = get_settings()
    role = settings.app_db_role

    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {role};"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public "
        f"REVOKE USAGE, SELECT ON SEQUENCES FROM {role};"
    )
    op.execute("DROP FUNCTION IF EXISTS app_current_user_id();")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {role};")
    # The role itself is intentionally NOT dropped: it is cluster-global and may
    # own grants in sibling databases (e.g. sentinel_test).
