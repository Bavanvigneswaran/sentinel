"""Alert rules, their per-(rule, device) evaluation state, firing history, and
silences.

Four tables, three different foreign-key shapes, on purpose:

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
    metric: Mapped[str] = mapped_column(sa.Text, nullable=False)
    comparison: Mapped[str] = mapped_column(sa.Text, nullable=False)
    threshold: Mapped[float] = mapped_column(sa.Float, nullable=False)
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

    `rule_name`/`metric`/`comparison`/`threshold` are snapshotted from the rule
    at the moment it fired, so history reads correctly even after the rule is
    later edited or deleted.
    """

    __tablename__ = "alert_events"
    __table_args__ = (
        sa.CheckConstraint(in_check("status", EVENT_STATUSES), name="status"),
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

    rule_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    metric: Mapped[str] = mapped_column(sa.Text, nullable=False)
    comparison: Mapped[str] = mapped_column(sa.Text, nullable=False)
    threshold: Mapped[float] = mapped_column(sa.Float, nullable=False)

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
