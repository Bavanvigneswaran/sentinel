"""Alert rules, their per-(rule, device) evaluation state, firing history,
silences, and adaptive anomaly baselines.

`AlertRule.rule_type` discriminates three kinds of row in one table:
threshold rules set `comparison`/`threshold` and are judged by
analysis/alerts.py's evaluate_condition() against a live reading; forecast
rules set the same two fields but are judged by app/alerts/forecast_eval.py
against a stored MetricForecast row's 24h-ahead prediction instead of a live
one; anomaly rules leave both null and are judged by app/alerts/evaluator.py
against an AnomalyBaseline row, using analysis/anomaly.py's z-score math. All
three feed the same `step()` state machine and land in the same
AlertState/AlertEvent tables — a discriminator was chosen over a sibling
table because AlertState/AlertEvent already FK straight to a rule row; a
sibling table would need a polymorphic association to reuse them.

Five tables, three different foreign-key shapes, on purpose:

* `AlertRule.device_id` and `AlertState`/`AlertEvent`/`AlertSilence.device_id`
  are all *composite* FKs to `devices (id, user_id)`, the same
  tenancy-consistency pattern `metrics.py` and migration 0003 use everywhere
  else a row points at a device: it makes "this row's user_id disagrees with
  the device it points at" unrepresentable, which is what lets RLS trust a
  single denormalized `user_id` column instead of joining through devices.
* `AlertState.rule_id` and `AlertEvent.rule_id` are *plain* FKs to
  `alert_rules.id`, not composite. Composite FKs exist to reject a
  client-supplied tenant mismatch; these two tables are written only by the
  evaluator (app/alerts/evaluator.py) inside a session already scoped to the
  rule's own owner, so `rule.user_id` is correct by construction. A composite
  FK would also force `ON DELETE SET NULL` to null out `user_id` alongside
  `rule_id`, which would break the row's own RLS visibility the moment a rule
  is deleted — exactly the opposite of what a firing-history row needs.
* `AlertSilence.rule_id` is a plain FK with `ON DELETE CASCADE`: a silence for
  a rule that no longer exists is meaningless, unlike an event.
* `AlertEvent.incident_id` is a plain FK to `incidents.id`, `ON DELETE
  SET NULL`, for the same reason `rule_id` is plain: it is written only by
  app/alerts/incident_apply.py inside a session already scoped to the
  event's own owner, so `incident.user_id == event.user_id` is correct by
  construction. See app/models/incidents.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, in_check, ts_now, uuid_pk

#: The V1 alertable-metric catalog. Mirrors analysis/health.py's component
#: list minus network throughput/IOPS, which have no natural "up is bad"
#: absolute threshold for a first cut. Round-trip time IS included here even
#: though health.py deliberately excludes it from the health score — the
#: reason health.py gives (the target is user-chosen, so an absolute curve
#: would be arbitrary) does not apply to an alert rule, where the threshold
#: itself is the thing the user chooses.
METRICS = (
    "cpu_percent",
    "mem_percent",
    "swap_percent",
    "disk_percent",
    "packet_loss_percent",
    "cpu_iowait_percent",
)
COMPARISONS = (">", ">=", "<", "<=", "==")
RULE_TYPES = ("threshold", "anomaly", "forecast")
#: Where a rule came from. "builtin" rows are seeded by
#: app/alerts/defaults.py so an account detects things without having been
#: configured; they are ordinary rules in every other respect, including
#: being editable and deletable.
RULE_SOURCES = ("user", "builtin")
ALERT_STATES = ("ok", "pending", "firing")
EVENT_STATUSES = ("firing", "resolved")


def _device_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["device_id", "user_id"],
        ["devices.id", "devices.user_id"],
        name=f"fk_{table}_device_id_user_id_devices",
        ondelete="CASCADE",
    )


class AlertRule(TimestampMixin, Base):
    __tablename__ = "alert_rules"
    __table_args__ = (
        sa.CheckConstraint(in_check("metric", METRICS), name="metric"),
        sa.CheckConstraint(in_check("comparison", COMPARISONS), name="comparison"),
        sa.CheckConstraint(in_check("rule_type", RULE_TYPES), name="rule_type"),
        sa.CheckConstraint(in_check("source", RULE_SOURCES), name="source"),
        sa.CheckConstraint(
            "(rule_type IN ('threshold', 'forecast') AND threshold IS NOT NULL "
            "AND comparison IS NOT NULL) "
            "OR (rule_type = 'anomaly' AND threshold IS NULL AND comparison IS NULL)",
            name="rule_type_fields",
        ),
        sa.CheckConstraint("for_duration_seconds >= 0", name="for_duration_seconds_non_negative"),
        _device_fk("alert_rules"),
        sa.Index("ix_alert_rules_user_id", "user_id"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: None applies the rule to every one of the user's devices.
    device_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, nullable=True)

    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    #: "threshold" and "forecast" rules set comparison/threshold below;
    #: "anomaly" rules leave both null and are judged against an
    #: AnomalyBaseline row instead. See module docstring.
    rule_type: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'threshold'")
    )
    #: "user" for a hand-written rule, "builtin" for one from
    #: app/alerts/defaults.py. Presentation only — the evaluator does not read
    #: it, and a builtin rule is edited and deleted like any other.
    source: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'user'")
    )
    metric: Mapped[str] = mapped_column(sa.Text, nullable=False)
    comparison: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    threshold: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    #: How long `comparison` must hold continuously before PENDING becomes
    #: FIRING. See app/analysis/alerts.py for the state machine this drives.
    for_duration_seconds: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("60")
    )
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())


class AlertState(Base):
    """One row per (rule, device) pair the evaluator has actually evaluated at
    least once, created lazily on first evaluation — a fleet-wide rule against
    five devices produces five rows, not one."""

    __tablename__ = "alert_states"
    __table_args__ = (
        sa.CheckConstraint(in_check("state", ALERT_STATES), name="state"),
        _device_fk("alert_states"),
        sa.UniqueConstraint("rule_id", "device_id", name="uq_alert_states_rule_id_device_id"),
        sa.Index("ix_alert_states_user_id", "user_id"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)

    state: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'ok'")
    )
    #: When the current PENDING streak began. None outside PENDING.
    pending_since: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: The most recent fresh value this pair was evaluated against.
    last_value: Mapped[float | None] = mapped_column(sa.Float)
    #: The open AlertEvent while state == "firing"; None otherwise.
    current_event_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("alert_events.id", ondelete="SET NULL"), nullable=True
    )
    last_evaluated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class AlertEvent(Base):
    """One row per firing episode — the alerts triage page's data source.

    `rule_name`/`rule_type`/`metric`/`comparison`/`threshold` are snapshotted
    from the rule at the moment it fired, so history reads correctly even
    after the rule is later edited or deleted — `rule_type` is the reliable
    discriminator for a reader, since `rule_id` may be null after the rule
    itself is deleted and, since forecast rules also set comparison/
    threshold, `comparison IS NULL` alone no longer distinguishes every kind.
    `comparison`/`threshold` are null for an anomaly-sourced event (no
    operator, no fixed threshold in that sense) — `observed_value`/
    `baseline_mean`/`baseline_mad`/`z_score` carry the equivalent evidence
    instead. `predicted_breach_at`/`predicted_value` carry the equivalent for
    a forecast-sourced event. All four evidence columns are null for a
    plain threshold-sourced event.
    """

    __tablename__ = "alert_events"
    __table_args__ = (
        sa.CheckConstraint(in_check("status", EVENT_STATUSES), name="status"),
        sa.CheckConstraint(in_check("rule_type", RULE_TYPES), name="rule_type"),
        _device_fk("alert_events"),
        sa.Index("ix_alert_events_user_id_status_fired_at", "user_id", "status", "fired_at"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    #: The incident this event was correlated into — see
    #: app/alerts/incident_apply.py. Set when the event fires, read back when
    #: it resolves to decide whether the incident closes too.
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True
    )

    rule_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    rule_type: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'threshold'")
    )
    metric: Mapped[str] = mapped_column(sa.Text, nullable=False)
    comparison: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    threshold: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'firing'")
    )
    value_at_fire: Mapped[float] = mapped_column(sa.Float, nullable=False)
    #: Updated on every tick the pair is re-evaluated while firing.
    last_value: Mapped[float | None] = mapped_column(sa.Float)
    fired_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    resolved_value: Mapped[float | None] = mapped_column(sa.Float)
    #: None means "never notified" — either still firing-but-unattempted for
    #: one tick, or silenced. Set on a best-effort dispatch attempt regardless
    #: of per-channel success, so a persistently broken channel cannot turn
    #: into an infinite per-tick retry storm.
    notified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    #: The four fields below are populated only for an anomaly-sourced fire,
    #: snapshotting the baseline exactly as it stood right before the
    #: anomalous reading was folded into it (see evaluator.py's
    #: judge-before-update ordering) — the authoritative record of "what
    #: normal looked like" at the moment this fired, independent of how the
    #: live AnomalyBaseline row may have since drifted.
    observed_value: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    baseline_mean: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    baseline_mad: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    z_score: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    #: Populated only for a forecast-sourced fire: the first forecast point
    #: that satisfied the rule's comparison, and its predicted timestamp.
    predicted_breach_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    predicted_value: Mapped[float | None] = mapped_column(sa.Float, nullable=True)


class AlertSilence(Base):
    """Suppresses notifications for a window of time. Does not affect the
    state machine — a silenced alert still shows as FIRING on the triage page,
    it just does not page anyone. Immutable once created (no updated_at):
    change a silence by deleting and recreating it.
    """

    __tablename__ = "alert_silences"
    __table_args__ = (
        _device_fk("alert_silences"),
        sa.Index("ix_alert_silences_user_id", "user_id"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: None silences every device / every rule respectively.
    device_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, nullable=True)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=True
    )

    starts_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[ts_now]


class AnomalyBaseline(Base):
    """One row per (device, metric) an anomaly rule has ever evaluated
    against that device, created lazily on first evaluation — same posture
    as AlertState. `mean`/`mad`/`sample_count` are exactly
    analysis/anomaly.py's BaselineState fields; app/alerts/evaluator.py is
    the only writer, updating this once per tick regardless of whether that
    tick fired, so the baseline keeps adapting even while an alert is open.
    """

    __tablename__ = "anomaly_baselines"
    __table_args__ = (
        sa.CheckConstraint(in_check("metric", METRICS), name="metric"),
        sa.CheckConstraint("sample_count >= 0", name="sample_count_non_negative"),
        _device_fk("anomaly_baselines"),
        sa.UniqueConstraint("device_id", "metric", name="uq_anomaly_baselines_device_id_metric"),
        sa.Index("ix_anomaly_baselines_user_id", "user_id"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    metric: Mapped[str] = mapped_column(sa.Text, nullable=False)

    mean: Mapped[float] = mapped_column(sa.Float, nullable=False)
    #: EWMA of absolute deviation, unscaled — see analysis/anomaly.py.
    mad: Mapped[float] = mapped_column(sa.Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
