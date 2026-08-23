"""Pure "is this schedule due right now" arithmetic, no I/O — the one piece
of real logic app/workers/report_worker.py needs, split out the same way
analysis/alerts.py's step() is split from app/alerts/evaluator.py.

A schedule is due on exactly one calendar day per cadence (the weekday for
"weekly", the day-of-month for "monthly"), and at most once on that day.
`is_due()` takes `now` and `last_sent_at` rather than reading the clock
itself, so the worker's hourly sweep can call it repeatedly through the day
without resending: once `last_sent_at` lands on the same UTC calendar date
as `now`, the schedule stops being due until its next scheduled day arrives.
"""

from __future__ import annotations

from datetime import datetime


def is_due(
    *,
    cadence: str,
    day_of_week: int | None,
    day_of_month: int | None,
    last_sent_at: datetime | None,
    now: datetime,
) -> bool:
    if last_sent_at is not None and last_sent_at.date() == now.date():
        return False
    if cadence == "weekly":
        return day_of_week is not None and now.weekday() == day_of_week
    if cadence == "monthly":
        return day_of_month is not None and now.day == day_of_month
    raise ValueError(f"unknown cadence: {cadence!r}")
