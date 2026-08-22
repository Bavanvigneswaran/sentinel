"""anomaly rules

Adds the "anomaly" rule_type to alert_rules alongside the existing
"threshold" rules (see app/models/alerts.py's module docstring), the
matching evidence columns on alert_events, and a new anomaly_baselines table
holding the live EWMA/MAD state app/alerts/evaluator.py maintains per
(device, metric). RLS on the new table follows the exact pattern established
in 0006_alerts_and_notifications.py.

`rule_type`'s backfill is free: ADD COLUMN ... NOT NULL DEFAULT 'threshold'
populates every existing alert_rules row from the server_default as part of
the same DDL statement, so every pre-existing row already satisfies both new
CHECK constraints (its comparison/threshold were NOT NULL under the old
schema) by the time they're validated.

Revision ID: 0007_anomaly_rules
Revises: 0006_alerts_and_notifications
Create Date: 2026-08-22 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_anomaly_rules"
down_revision: str | Sequence[str] | None = "0006_alerts_and_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Only the new table needs an RLS policy here — alert_rules/alert_events
#: already carry one from 0006 and this migration doesn't touch that.
TENANT_TABLES = ("anomaly_baselines",)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "alert_rules",
        sa.Column("rule_type", sa.Text(), server_default=sa.text("'threshold'"), nullable=False),
    )
    op.alter_column("alert_rules", "comparison", nullable=True)
    op.alter_column("alert_rules", "threshold", nullable=True)
    op.create_check_constraint(
        op.f("ck_alert_rules_rule_type"), "alert_rules", "rule_type IN ('threshold', 'anomaly')"
    )
    op.create_check_constraint(
        op.f("ck_alert_rules_rule_type_fields"),
        "alert_rules",
        "(rule_type = 'threshold' AND threshold IS NOT NULL AND comparison IS NOT NULL) "
        "OR (rule_type = 'anomaly' AND threshold IS NULL AND comparison IS NULL)",
    )

    op.alter_column("alert_events", "comparison", nullable=True)
    op.alter_column("alert_events", "threshold", nullable=True)
    for column in ("observed_value", "baseline_mean", "baseline_mad", "z_score"):
        op.add_column("alert_events", sa.Column(column, sa.Float(), nullable=True))

    op.create_table(
        "anomaly_baselines",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("mean", sa.Float(), nullable=False),
        sa.Column("mad", sa.Float(), nullable=False),
        sa.Column("sample_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "metric IN ('cpu_percent', 'mem_percent', 'swap_percent', 'disk_percent', "
            "'packet_loss_percent', 'cpu_iowait_percent')",
            name=op.f("ck_anomaly_baselines_metric"),
        ),
        sa.CheckConstraint(
            "sample_count >= 0", name=op.f("ck_anomaly_baselines_sample_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["device_id", "user_id"],
            ["devices.id", "devices.user_id"],
            name="fk_anomaly_baselines_device_id_user_id_devices",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_anomaly_baselines_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anomaly_baselines")),
        sa.UniqueConstraint(
            "device_id", "metric", name=op.f("uq_anomaly_baselines_device_id_metric")
        ),
    )
    op.create_index(
        "ix_anomaly_baselines_user_id", "anomaly_baselines", ["user_id"], unique=False
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

    op.drop_index("ix_anomaly_baselines_user_id", table_name="anomaly_baselines")
    op.drop_table("anomaly_baselines")

    for column in ("observed_value", "baseline_mean", "baseline_mad", "z_score"):
        op.drop_column("alert_events", column)
    op.alter_column("alert_events", "threshold", nullable=False)
    op.alter_column("alert_events", "comparison", nullable=False)

    op.drop_constraint(op.f("ck_alert_rules_rule_type_fields"), "alert_rules", type_="check")
    op.drop_constraint(op.f("ck_alert_rules_rule_type"), "alert_rules", type_="check")
    op.alter_column("alert_rules", "threshold", nullable=False)
    op.alter_column("alert_rules", "comparison", nullable=False)
    op.drop_column("alert_rules", "rule_type")
