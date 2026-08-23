"""Scheduled report configuration.

One row per recurring report a user has set up: which device (or the whole
fleet), how wide a trailing window each run covers, how often it repeats, and
who receives it. `app/workers/report_worker.py` is the only writer of
`last_sent_at`; `app/analysis/report_schedule.py`'s `is_due()` is the pure
function that decides, from this row alone plus the current time, whether
today is a send day — mirroring anomaly/forecast's split between a pure
analysis module and the worker that drives it off a DB row.

There is no `GeneratedReport` history table: a report is generated on demand
(an API download, or an outbound email) and never persisted server-side,
the same best-effort, nothing-to-replay posture `app/alerts/notify.py`
already takes for alert notifications — the source data (metrics, alerts,
incidents) is the durable record, not a rendered snapshot of it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, in_check, uuid_pk

REPORT_FORMATS = ("pdf", "csv")
REPORT_CADENCES = ("weekly", "monthly")


class ReportSchedule(TimestampMixin, Base):
    __tablename__ = "report_schedules"
    __table_args__ = (
        sa.CheckConstraint(in_check("cadence", REPORT_CADENCES), name="cadence"),
        sa.CheckConstraint(in_check("format", REPORT_FORMATS), name="format"),
        sa.CheckConstraint("period_days > 0", name="period_days_positive"),
        sa.CheckConstraint(
            "(cadence = 'weekly' AND day_of_week IS NOT NULL AND day_of_month IS NULL) OR "
            "(cadence = 'monthly' AND day_of_month IS NOT NULL AND day_of_week IS NULL)",
            name="cadence_fields",
        ),
        sa.CheckConstraint(
            "day_of_week IS NULL OR day_of_week BETWEEN 0 AND 6", name="day_of_week_range"
        ),
        # Capped at 28 so "the 30th" never silently skips February.
        sa.CheckConstraint(
            "day_of_month IS NULL OR day_of_month BETWEEN 1 AND 28", name="day_of_month_range"
        ),
        sa.ForeignKeyConstraint(
            ["device_id", "user_id"],
            ["devices.id", "devices.user_id"],
            name="fk_report_schedules_device_id_user_id_devices",
            ondelete="CASCADE",
        ),
        sa.Index("ix_report_schedules_user_id", "user_id"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: None reports on the whole fleet, same convention as AlertRule.device_id.
    device_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, nullable=True)

    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    #: Trailing window each generated report covers, e.g. 7/30/90.
    period_days: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("30")
    )
    cadence: Mapped[str] = mapped_column(sa.Text, nullable=False)
    #: 0=Monday..6=Sunday (Python's datetime.weekday()), set only for "weekly".
    day_of_week: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    #: 1-28, set only for "monthly".
    day_of_month: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    format: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'pdf'"))
    #: Empty means "the account's own email", same fallback NotificationSettings
    #: uses for email_address.
    recipients: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())
    #: None means "never sent". Read by is_due(), written only by ReportWorker
    #: and the manual /reports/schedules/{id}/send-now route.
    last_sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
