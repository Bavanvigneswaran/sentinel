"""Pure due-date arithmetic in app/analysis/report_schedule.py — no DB, no I/O.
app/workers/report_worker.py's own tests exercise this wired to real
ReportSchedule rows.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.analysis.report_schedule import is_due

# 2026-08-24 is a Monday (weekday() == 0).
MONDAY = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
TUESDAY = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)


def test_weekly_due_on_matching_weekday_never_sent():
    assert is_due(cadence="weekly", day_of_week=0, day_of_month=None, last_sent_at=None, now=MONDAY)


def test_weekly_not_due_on_a_different_weekday():
    assert not is_due(
        cadence="weekly", day_of_week=0, day_of_month=None, last_sent_at=None, now=TUESDAY
    )


def test_weekly_not_due_again_same_calendar_day():
    already_sent = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
    assert not is_due(
        cadence="weekly", day_of_week=0, day_of_month=None, last_sent_at=already_sent, now=MONDAY
    )


def test_weekly_due_again_the_following_matching_weekday():
    sent_last_monday = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
    assert is_due(
        cadence="weekly",
        day_of_week=0,
        day_of_month=None,
        last_sent_at=sent_last_monday,
        now=MONDAY,
    )


def test_monthly_due_on_matching_day_of_month():
    now = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    assert is_due(cadence="monthly", day_of_week=None, day_of_month=1, last_sent_at=None, now=now)


def test_monthly_not_due_on_a_different_day_of_month():
    now = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
    assert not is_due(
        cadence="monthly", day_of_week=None, day_of_month=1, last_sent_at=None, now=now
    )


def test_monthly_not_due_again_same_calendar_day():
    now = datetime(2026, 8, 1, 18, 0, tzinfo=UTC)
    already_sent = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    assert not is_due(
        cadence="monthly", day_of_week=None, day_of_month=1, last_sent_at=already_sent, now=now
    )


def test_unknown_cadence_raises():
    with pytest.raises(ValueError):
        is_due(cadence="daily", day_of_week=None, day_of_month=None, last_sent_at=None, now=MONDAY)
