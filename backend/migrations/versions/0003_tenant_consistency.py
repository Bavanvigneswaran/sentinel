"""tenant-consistency FKs and database CONNECT lockdown

`agent_tokens.user_id` and `enrollment_codes.user_id` are denormalized from
`devices` so the RLS policies are a single indexed predicate with no join.
Nothing previously stopped those columns from disagreeing with the owner of the
referenced device — and since RLS scopes by the denormalized column, a mismatch
would expose the row to the wrong tenant. Composite foreign keys make that
state unrepresentable.

Revision ID: 0003_tenant_consistency
Revises: 0002_auth_schema
"""

from __future__ import annotations

from alembic import op

from app.config import get_settings

revision: str = "0003_tenant_consistency"
down_revision: str | None = "0002_auth_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Must exist before anything can reference (id, user_id). Redundant against
    # the primary key, but a composite FK needs a matching unique constraint.
    op.create_unique_constraint("uq_devices_id_user_id", "devices", ["id", "user_id"])

    op.drop_constraint("fk_agent_tokens_device_id_devices", "agent_tokens", type_="foreignkey")
    op.create_foreign_key(
        "fk_agent_tokens_device_id_user_id_devices",
        "agent_tokens",
        "devices",
        ["device_id", "user_id"],
        ["id", "user_id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "fk_enrollment_codes_device_id_devices", "enrollment_codes", type_="foreignkey"
    )
    # The column-list form of SET NULL (PostgreSQL 15+) nulls only device_id, so
    # user_id stays NOT NULL and the audit row survives a device deletion.
    # Composite FKs are MATCH SIMPLE, so a NULL device_id skips the check —
    # correct for a code that has not been consumed yet.
    op.create_foreign_key(
        "fk_enrollment_codes_device_id_user_id_devices",
        "enrollment_codes",
        "devices",
        ["device_id", "user_id"],
        ["id", "user_id"],
        ondelete="SET NULL (device_id)",
    )

    # PostgreSQL grants CONNECT on every database to PUBLIC by default, so any
    # role created on this cluster could open a connection here. Only the owner
    # and the application role have any business doing so.
    settings = get_settings()
    role = settings.app_db_role
    if not role.isidentifier():
        raise ValueError(f"app_db_role must be a plain identifier, got {role!r}")

    # GRANT/REVOKE ON DATABASE needs a literal name, so resolve it at runtime.
    # format(%I) applies quote_ident, which is injection-safe.
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), '{role}');
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format(
                'GRANT CONNECT, TEMPORARY ON DATABASE %I TO PUBLIC', current_database()
            );
        END
        $$;
        """
    )

    op.drop_constraint(
        "fk_enrollment_codes_device_id_user_id_devices", "enrollment_codes", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_enrollment_codes_device_id_devices",
        "enrollment_codes",
        "devices",
        ["device_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint(
        "fk_agent_tokens_device_id_user_id_devices", "agent_tokens", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_agent_tokens_device_id_devices",
        "agent_tokens",
        "devices",
        ["device_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("uq_devices_id_user_id", "devices", type_="unique")
