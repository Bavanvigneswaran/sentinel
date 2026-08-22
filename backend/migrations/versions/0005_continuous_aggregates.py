"""continuous aggregates at 1m/5m/1h, with a tenant-scoped wrapper view per rollup

Everything here is hand-written; there is nothing for Alembic to autogenerate,
because a continuous aggregate is not a table in Base.metadata.

Two TimescaleDB facts shape this migration, both verified against 2.28 on PG16:

1. **A continuous aggregate cannot be created on a hypertable that has row-level
   security enabled** — `ERROR: cannot create continuous aggregate on hypertable
   with row security`. So RLS is switched off around the CREATE and switched
   back on immediately afterwards. Both statements take an ACCESS EXCLUSIVE lock
   and the whole migration runs in one transaction, so no other session can read
   the table while the policy is off.

2. **A continuous aggregate does not support `security_invoker`** — the view is
   owned by the table owner, so a restricted role selecting from it reads *every
   tenant's* rows. Measured, not assumed: an unwrapped rollup returned two
   distinct user_ids to a NOBYPASSRLS role whose raw-table query correctly
   returned one.

Hence the shape below. The aggregate is named `cagg_<table>_<tier>` and the app
role is explicitly revoked from it (migration 0001's ALTER DEFAULT PRIVILEGES
would otherwise have granted it automatically). What the application queries is
`<table>_<tier>`: a `security_barrier` view carrying an unconditional
`user_id = app_current_user_id()` predicate. Same default-deny as an RLS policy —
an unset GUC yields NULL, `user_id = NULL` matches nothing — and it cannot be
forgotten at the call site, because the call site has no other object to name.

Revision ID: 0005_continuous_aggregates
Revises: 0004_metrics_hypertables
Create Date: 2026-08-22

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_continuous_aggregates"
down_revision: str | Sequence[str] | None = "0004_metrics_hypertables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

from app.config import get_settings  # noqa: E402
from app.models.rollups import (  # noqa: E402
    DOMAINS,
    MATERIALIZED_ONLY,
    RAW_COUNT,
    RAW_WEIGHT,
    REFRESH_START_OFFSET,
    TIERS,
    RollupDomain,
    RollupTier,
    select_list,
)


def _app_role() -> str:
    role = get_settings().app_db_role
    if not role.isidentifier():
        raise ValueError(f"app_db_role must be a plain identifier, got {role!r}")
    return role


def _create_cagg(domain: RollupDomain, tier: RollupTier) -> None:
    bucket = f"time_bucket(INTERVAL '{tier.bucket}', ts)"
    # user_id is grouped, not just carried: it is the wrapper view's tenancy
    # predicate, and a metric row's user_id is pinned to its device by the
    # composite FK in 0004, so grouping on it cannot split a device's series.
    keys = ["device_id", "user_id", *domain.entity_keys]
    columns = [*keys, f"{bucket} AS ts", *select_list(domain, weight=RAW_WEIGHT, count=RAW_COUNT)]

    op.execute(
        f"""
        CREATE MATERIALIZED VIEW {domain.cagg_name(tier)}
        WITH (
            timescaledb.continuous,
            timescaledb.materialized_only = {str(MATERIALIZED_ONLY).lower()}
        ) AS
        SELECT {", ".join(columns)}
        FROM {domain.source}
        GROUP BY {", ".join([*keys, bucket])}
        WITH NO DATA;
        """
    )


def _create_scoped_view(domain: RollupDomain, tier: RollupTier, role: str) -> None:
    cagg = domain.cagg_name(tier)
    view = domain.view_name(tier)

    # The aggregate itself is owner-only. Without this REVOKE the app role would
    # inherit SELECT from 0001's default privileges and could read across tenants.
    op.execute(f"REVOKE ALL ON {cagg} FROM PUBLIC;")
    op.execute(f"REVOKE ALL ON {cagg} FROM {role};")

    # security_barrier stops a cheap user-supplied operator being pushed below
    # the tenancy predicate, which is the classic way a filtering view leaks the
    # rows it was meant to hide.
    op.execute(
        f"""
        CREATE VIEW {view} WITH (security_barrier = true) AS
        SELECT * FROM {cagg} WHERE user_id = app_current_user_id();
        """
    )
    # Read-only by intent as well as by construction: a view over a continuous
    # aggregate is not auto-updatable, but default privileges hand out the write
    # bits anyway and an unusable grant is still a misleading one.
    op.execute(f"REVOKE ALL ON {view} FROM PUBLIC;")
    op.execute(f"REVOKE ALL ON {view} FROM {role};")
    op.execute(f"GRANT SELECT ON {view} TO {role};")


def _add_policies(domain: RollupDomain, tier: RollupTier) -> None:
    cagg = domain.cagg_name(tier)
    op.execute(
        f"""
        SELECT add_continuous_aggregate_policy('{cagg}',
            start_offset => INTERVAL '{REFRESH_START_OFFSET}',
            end_offset => INTERVAL '{tier.refresh_end_offset}',
            schedule_interval => INTERVAL '{tier.refresh_schedule}');
        """
    )
    op.execute(f"SELECT add_retention_policy('{cagg}', INTERVAL '{tier.retention}');")


def upgrade() -> None:
    role = _app_role()

    for domain in DOMAINS:
        # See the module docstring: the CREATE is refused while RLS is on. The
        # window is closed again before this transaction commits, and ALTER
        # TABLE's ACCESS EXCLUSIVE lock keeps every other session out of the
        # table for its duration.
        op.execute(f"ALTER TABLE {domain.source} DISABLE ROW LEVEL SECURITY;")
        for tier in TIERS:
            _create_cagg(domain, tier)
        op.execute(f"ALTER TABLE {domain.source} ENABLE ROW LEVEL SECURITY;")

        for tier in TIERS:
            _create_scoped_view(domain, tier, role)
            _add_policies(domain, tier)


def downgrade() -> None:
    for domain in DOMAINS:
        for tier in TIERS:
            cagg = domain.cagg_name(tier)
            # Jobs outlive their aggregate unless removed explicitly, exactly as
            # the raw retention policies do in 0004.
            op.execute(
                f"SELECT remove_continuous_aggregate_policy('{cagg}', if_exists => true);"
            )
            op.execute(f"SELECT remove_retention_policy('{cagg}', if_exists => true);")
            op.execute(f"DROP VIEW IF EXISTS {domain.view_name(tier)};")
            op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {cagg};")
