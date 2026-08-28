"""A fourth rule type, so layer 4 can alert.

The multivariate novelty model (app/analysis/multivariate.py) could be read at
GET /devices/{id}/novelty and nowhere else — it detected and then told nobody.
This lets a rule be written against it, and deliberately does so by widening
the existing machinery rather than adding a parallel one: a multivariate rule
is an ordinary `alert_rules` row, judged in the same evaluator sweep, through
the same OK/PENDING/FIRING state machine, correlated into incidents and
notified by the same code as the other three. That is the refusal to add a
second evaluation path that Phases 6, 7 and 8 each made.

Three constraint changes, no new tables and no new columns:

* `rule_type` gains 'multivariate', on alert_rules and alert_events alike.
* `rule_type_fields` puts it in the comparison/threshold branch — it is judged
  like a threshold rule, just against a score rather than a reading.
* `metric` gains the reserved value 'novelty_score', and a new
  `multivariate_metric` CHECK makes the type and that metric imply each other
  in both directions. Without it a *threshold* rule could be pointed at
  novelty_score: it would read plausibly, be accepted, and silently never fire,
  because nothing writes that column.

Deliberately unchanged: anomaly_baselines and the two forecast tables keep
their own, narrower METRICS constraint, so nothing can create a baseline or a
forecast for a metric that is a model output rather than a measurement.

Revision ID: 0015_multivariate_rules
Revises: 0014_builtin_alert_rules
Create Date: 2026-08-27 05:40:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_multivariate_rules"
down_revision: str | Sequence[str] | None = "0014_builtin_alert_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_METRICS = (
    "cpu_percent",
    "mem_percent",
    "swap_percent",
    "disk_percent",
    "packet_loss_percent",
    "cpu_iowait_percent",
)
_NOVELTY_METRIC = "novelty_score"


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    op.drop_constraint("rule_type", "alert_rules", type_="check")
    op.create_check_constraint(
        "rule_type",
        "alert_rules",
        _in_list("rule_type", ("threshold", "anomaly", "forecast", "multivariate")),
    )

    op.drop_constraint("rule_type", "alert_events", type_="check")
    op.create_check_constraint(
        "rule_type",
        "alert_events",
        _in_list("rule_type", ("threshold", "anomaly", "forecast", "multivariate")),
    )

    op.drop_constraint("metric", "alert_rules", type_="check")
    op.create_check_constraint(
        "metric", "alert_rules", _in_list("metric", (*_METRICS, _NOVELTY_METRIC))
    )

    op.drop_constraint("rule_type_fields", "alert_rules", type_="check")
    op.create_check_constraint(
        "rule_type_fields",
        "alert_rules",
        "(rule_type IN ('threshold', 'forecast', 'multivariate') "
        "AND threshold IS NOT NULL AND comparison IS NOT NULL) "
        "OR (rule_type = 'anomaly' AND threshold IS NULL AND comparison IS NULL)",
    )

    op.create_check_constraint(
        "multivariate_metric",
        "alert_rules",
        "(rule_type = 'multivariate') = (metric = 'novelty_score')",
    )


def downgrade() -> None:
    # Any multivariate rule has to go before the narrower constraints can be
    # restored — it is unrepresentable under them, and its events would fail
    # the rule_type CHECK too. Deleting the rules cascades to their states;
    # the events are deleted explicitly because their FK is SET NULL.
    op.execute("DELETE FROM alert_events WHERE rule_type = 'multivariate'")
    op.execute("DELETE FROM alert_rules WHERE rule_type = 'multivariate'")

    op.drop_constraint("multivariate_metric", "alert_rules", type_="check")

    op.drop_constraint("rule_type_fields", "alert_rules", type_="check")
    op.create_check_constraint(
        "rule_type_fields",
        "alert_rules",
        "(rule_type IN ('threshold', 'forecast') AND threshold IS NOT NULL "
        "AND comparison IS NOT NULL) "
        "OR (rule_type = 'anomaly' AND threshold IS NULL AND comparison IS NULL)",
    )

    op.drop_constraint("metric", "alert_rules", type_="check")
    op.create_check_constraint("metric", "alert_rules", _in_list("metric", _METRICS))

    op.drop_constraint("rule_type", "alert_events", type_="check")
    op.create_check_constraint(
        "rule_type", "alert_events", _in_list("rule_type", ("threshold", "anomaly", "forecast"))
    )

    op.drop_constraint("rule_type", "alert_rules", type_="check")
    op.create_check_constraint(
        "rule_type", "alert_rules", _in_list("rule_type", ("threshold", "anomaly", "forecast"))
    )
