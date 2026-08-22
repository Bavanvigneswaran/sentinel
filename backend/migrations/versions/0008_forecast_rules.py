"""forecast rules

Adds the "forecast" rule_type to alert_rules alongside the existing
"threshold"/"anomaly" rules (see app/models/alerts.py's module docstring), a
rule_type snapshot plus predicted_breach_at/predicted_value evidence columns
on alert_events, and two new tables holding the live forecast/exhaustion
state app/workers/forecast_worker.py maintains per (device, metric). RLS on
the new tables follows the exact pattern established in
0007_anomaly_rules.py.

`alert_events.rule_type` cannot use a constant DEFAULT the way 0007's
alert_rules.rule_type backfill could: existing event rows are a mix of
threshold and anomaly fires, distinguishable only by `comparison IS NULL`
(the old, now-superseded heuristic this column replaces). The column is
added nullable, backfilled row-by-row from that heuristic, then tightened to
NOT NULL.

Revision ID: 0008_forecast_rules
Revises: 0007_anomaly_rules
Create Date: 2026-08-23 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008_forecast_rules"
down_revision: str | Sequence[str] | None = "0007_anomaly_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Only the two new tables need an RLS policy here — alert_rules/alert_events
#: already carry one from 0006 and this migration doesn't touch that.
TENANT_TABLES = ("metric_forecasts", "exhaustion_forecasts")


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f("ck_alert_rules_rule_type_fields"), "alert_rules", type_="check")
    op.drop_constraint(op.f("ck_alert_rules_rule_type"), "alert_rules", type_="check")
    op.create_check_constraint(
        op.f("ck_alert_rules_rule_type"),
        "alert_rules",
        "rule_type IN ('threshold', 'anomaly', 'forecast')",
    )
    op.create_check_constraint(
        op.f("ck_alert_rules_rule_type_fields"),
        "alert_rules",
        "(rule_type IN ('threshold', 'forecast') AND threshold IS NOT NULL "
        "AND comparison IS NOT NULL) "
        "OR (rule_type = 'anomaly' AND threshold IS NULL AND comparison IS NULL)",
    )

    op.add_column(
        "alert_events",
        sa.Column(
            "rule_type", sa.Text(), server_default=sa.text("'threshold'"), nullable=True
        ),
    )
    op.execute(
        "UPDATE alert_events SET rule_type = "
        "CASE WHEN comparison IS NULL THEN 'anomaly' ELSE 'threshold' END"
    )
    op.alter_column("alert_events", "rule_type", nullable=False)
    op.create_check_constraint(
        op.f("ck_alert_events_rule_type"),
        "alert_events",
        "rule_type IN ('threshold', 'anomaly', 'forecast')",
    )
    op.add_column(
        "alert_events", sa.Column("predicted_breach_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("alert_events", sa.Column("predicted_value", sa.Float(), nullable=True))

    op.create_table(
        "metric_forecasts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("entity", sa.Text(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("horizon_seconds", sa.Integer(), nullable=False),
        sa.Column("bucket_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "points",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "metric IN ('cpu_percent', 'mem_percent', 'swap_percent', 'disk_percent', "
            "'packet_loss_percent', 'cpu_iowait_percent')",
            name=op.f("ck_metric_forecasts_metric"),
        ),
        sa.ForeignKeyConstraint(
            ["device_id", "user_id"],
            ["devices.id", "devices.user_id"],
            name="fk_metric_forecasts_device_id_user_id_devices",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_metric_forecasts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_forecasts")),
        sa.UniqueConstraint(
            "device_id", "metric", name=op.f("uq_metric_forecasts_device_id_metric")
        ),
    )
    op.create_index("ix_metric_forecasts_user_id", "metric_forecasts", ["user_id"], unique=False)

    op.create_table(
        "exhaustion_forecasts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("entity", sa.Text(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("current_value", sa.Float(), nullable=False),
        sa.Column("slope_per_day", sa.Float(), nullable=False),
        sa.Column("projected_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "metric IN ('mem_percent', 'disk_percent')",
            name=op.f("ck_exhaustion_forecasts_metric"),
        ),
        sa.ForeignKeyConstraint(
            ["device_id", "user_id"],
            ["devices.id", "devices.user_id"],
            name="fk_exhaustion_forecasts_device_id_user_id_devices",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_exhaustion_forecasts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exhaustion_forecasts")),
        sa.UniqueConstraint(
            "device_id", "metric", name=op.f("uq_exhaustion_forecasts_device_id_metric")
        ),
    )
    op.create_index(
        "ix_exhaustion_forecasts_user_id", "exhaustion_forecasts", ["user_id"], unique=False
    )

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant ON {table}
            FOR ALL
            USING (user_id = app_current_user_id())
            WITH CHECK (user_id = app_current_user_id());
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    op.drop_index("ix_exhaustion_forecasts_user_id", table_name="exhaustion_forecasts")
    op.drop_table("exhaustion_forecasts")
    op.drop_index("ix_metric_forecasts_user_id", table_name="metric_forecasts")
    op.drop_table("metric_forecasts")

    op.drop_column("alert_events", "predicted_value")
    op.drop_column("alert_events", "predicted_breach_at")
    op.drop_constraint(op.f("ck_alert_events_rule_type"), "alert_events", type_="check")
    op.drop_column("alert_events", "rule_type")

    op.drop_constraint(op.f("ck_alert_rules_rule_type_fields"), "alert_rules", type_="check")
    op.drop_constraint(op.f("ck_alert_rules_rule_type"), "alert_rules", type_="check")
    op.create_check_constraint(
        op.f("ck_alert_rules_rule_type"), "alert_rules", "rule_type IN ('threshold', 'anomaly')"
    )
    op.create_check_constraint(
        op.f("ck_alert_rules_rule_type_fields"),
        "alert_rules",
        "(rule_type = 'threshold' AND threshold IS NOT NULL AND comparison IS NOT NULL) "
        "OR (rule_type = 'anomaly' AND threshold IS NULL AND comparison IS NULL)",
    )
