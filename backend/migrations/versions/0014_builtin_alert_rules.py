"""Detection that happens without being asked for.

Every detector in this system — the threshold evaluator, the adaptive anomaly
baselines, the forecast-breach check — already worked, and every one of them
sat idle until a user hand-wrote a rule. A new account with machines enrolled
and nothing configured was silent through a disk filling to 100%: not a
failure to detect, a failure to have been asked to look.

This adds `alert_rules.source` so a rule the product created can be told apart
from a rule a person wrote (for grouping and explanation in the UI — not for
locking it: a default you cannot disable is a worse default), and seeds the
default set for every account that already exists. New accounts get theirs at
signup instead; see app/alerts/defaults.py.

The backfill deliberately skips any account that already has a builtin rule,
so re-running it can never resurrect a default somebody deleted.

Revision ID: 0014_builtin_alert_rules
Revises: 0013_device_name_soft_delete
Create Date: 2026-08-25 22:10:00.000000+00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_builtin_alert_rules"
down_revision: str | Sequence[str] | None = "0013_device_name_soft_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Mirrors DEFAULT_RULES in app/alerts/defaults.py. Spelled out here rather
#: than imported: a migration has to keep describing the schema as it was at
#: this revision, and importing application code would make an old migration
#: change meaning every time that tuple is edited.
_DEFAULTS = [
    ("CPU pinned", "threshold", "cpu_percent", ">", 90, 600),
    ("Memory nearly full", "threshold", "mem_percent", ">", 90, 600),
    ("Disk nearly full", "threshold", "disk_percent", ">", 90, 600),
    ("Network dropping packets", "threshold", "packet_loss_percent", ">", 20, 300),
    ("Unusual CPU for this machine", "anomaly", "cpu_percent", None, None, 300),
    ("Unusual memory for this machine", "anomaly", "mem_percent", None, None, 300),
    ("Disk predicted to fill", "forecast", "disk_percent", ">", 90, 0),
    ("Memory predicted to fill", "forecast", "mem_percent", ">", 90, 0),
]


def upgrade() -> None:
    op.add_column(
        "alert_rules",
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'user'"),
        ),
    )
    op.create_check_constraint(
        "source", "alert_rules", "source IN ('user', 'builtin')"
    )

    # Seed every existing account. `NOT EXISTS` rather than a plain insert so
    # this is safe to re-run and cannot duplicate.
    for name, rule_type, metric, comparison, threshold, duration in _DEFAULTS:
        op.execute(
            sa.text(
                """
                INSERT INTO alert_rules (
                    id, user_id, device_id, name, rule_type, metric,
                    comparison, threshold, for_duration_seconds, enabled,
                    source, created_at, updated_at
                )
                SELECT
                    gen_random_uuid(), u.id, NULL, :name, :rule_type, :metric,
                    :comparison, :threshold, :duration, true,
                    'builtin', now(), now()
                FROM users u
                WHERE NOT EXISTS (
                    SELECT 1 FROM alert_rules r
                    WHERE r.user_id = u.id AND r.source = 'builtin'
                      AND r.name = :name
                )
                """
            ).bindparams(
                name=name,
                rule_type=rule_type,
                metric=metric,
                comparison=comparison,
                threshold=threshold,
                duration=duration,
            )
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM alert_rules WHERE source = 'builtin'"))
    op.drop_constraint("ck_alert_rules_source", "alert_rules", type_="check")
    op.drop_column("alert_rules", "source")
