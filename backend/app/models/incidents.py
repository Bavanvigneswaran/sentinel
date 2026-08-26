"""Incidents: a per-device grouping of correlated AlertEvents, plus the
cached AI-generated explanation of each one.

An incident is opened the moment any AlertEvent (of any rule_type) fires on a
device with no incident already open, and closed the moment none of its
attached events are still firing — see app/alerts/incident_apply.py, called
from state_apply.py's apply_step_result() right beside the AlertEvent
open/close bookkeeping it mirrors. Device-level, not rule-level: two
unrelated rules firing on the same machine at once are exactly the case an
"incidents workspace" exists to group, per CLAUDE.md's Phase 8 description.

`summary_text`/`root_cause_text` hold generated prose, stored purely as
display text and never parsed back into a decision. `*_model` records what
produced it — Phase 8 wrote a Claude model id there, and since the switch to
local templates it carries app/insights/generator.py's GENERATOR_ID, so rows
from either era stay distinguishable.

The `*_hash` columns are analysis/incidents.py's fingerprint over the
incident's own correlated-event membership at the moment each was generated;
the insights worker recomputes the fingerprint every tick and only
regenerates when it has actually changed. That began as a way to avoid a
periodic API bill and is kept for what survives the bill going away: it is
what makes `*_generated_at` mean "when this explanation was reached" rather
than "when a sweep last ran." See app/insights/service.py.

The text stays *stored* rather than computed at read time — tempting now
that generation is free and pure, and wrong: the bundle it is generated from
(health score, forecast rows) drifts, so a resolved incident recomputed
today would describe the machine's present state instead of the incident's.
Same reasoning AnomalyEvidenceChart draws the snapshotted and live baselines
as two separate lines.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, in_check, uuid_pk

INCIDENT_STATUSES = ("open", "resolved")


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        sa.CheckConstraint(in_check("status", INCIDENT_STATUSES), name="status"),
        sa.ForeignKeyConstraint(
            ["device_id", "user_id"],
            ["devices.id", "devices.user_id"],
            name="fk_incidents_device_id_user_id_devices",
            ondelete="CASCADE",
        ),
        # At most one open incident per device — a second concurrent fire on
        # the same device attaches to the existing one rather than racing to
        # open a sibling. Postgres partial unique index, enforced at the DB
        # layer rather than trusted to incident_apply.py's own read-then-write.
        sa.Index(
            "uq_incidents_one_open_per_device",
            "device_id",
            unique=True,
            postgresql_where=sa.text("status = 'open'"),
        ),
        sa.Index("ix_incidents_user_id_status_opened_at", "user_id", "status", "opened_at"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)

    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'open'"))
    opened_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    #: Haiku's short plain-English summary, and the fingerprint of the event
    #: membership it was generated from — see analysis/incidents.py's
    #: correlation_fingerprint().
    summary_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    summary_model: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    summary_generated_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    summary_signal_hash: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    #: Sonnet's deeper root-cause analysis, cached the same way.
    root_cause_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    root_cause_model: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    root_cause_generated_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    root_cause_signal_hash: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
