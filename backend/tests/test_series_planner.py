"""Source selection for the time-range API.

Pure arithmetic, no database. The planner is what decides whether a chart is
drawn from 1-second readings or from hour-wide averages, so its edges are worth
pinning down: retention, density, and the bucket-alignment rule that keeps the
re-aggregation exact.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.series_service import (
    MAX_WINDOW_DAYS,
    WindowTooWide,
    plan_series,
)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _plan(**kwargs):
    span = kwargs.pop("span")
    return plan_series(NOW - span, NOW, now=NOW, **kwargs)


def test_a_short_window_is_served_from_raw():
    plan = _plan(span=timedelta(minutes=30))
    assert plan.source.name == "raw"
    assert plan.bucket_seconds == 10


def test_a_day_moves_to_the_minute_rollup():
    plan = _plan(span=timedelta(hours=24))
    assert plan.source.name == "1m"
    # 86400s over 720 points needs 120s buckets, which is two whole 1m rows.
    assert plan.bucket_seconds == 120
    assert plan.estimated_points == 720


def test_a_month_uses_the_hour_rollup_because_it_is_the_cheapest_that_fits():
    plan = _plan(span=timedelta(days=30))
    assert plan.source.name == "1h"
    assert plan.bucket_seconds == 3600


def test_raw_is_refused_once_the_window_predates_its_retention():
    """Raw chunks are dropped after 7 days. A narrow window 30 days ago is well
    inside any point budget, but the rows are simply not there any more."""
    start = NOW - timedelta(days=30)
    plan = plan_series(start, start + timedelta(minutes=30), now=NOW)
    assert plan.source.name != "raw"
    assert plan.source.name == "1m"


def test_a_window_older_than_every_retention_falls_back_to_the_coarsest_source():
    start = NOW - timedelta(days=500)
    plan = plan_series(start, start + timedelta(days=1), now=NOW)
    assert plan.source.name == "1h"


def test_the_bucket_is_always_a_whole_multiple_of_the_source_resolution():
    """A bucket that split a source row would average a value across a boundary
    it was never measured over."""
    for span in (timedelta(hours=7), timedelta(days=3), timedelta(days=200)):
        plan = plan_series(NOW - span, NOW, now=NOW)
        assert plan.bucket_seconds % plan.source.resolution_seconds == 0


def test_a_tighter_point_budget_widens_the_bucket_rather_than_dropping_points():
    coarse = _plan(span=timedelta(hours=24), max_points=100)
    fine = _plan(span=timedelta(hours=24), max_points=720)
    assert coarse.bucket_seconds > fine.bucket_seconds
    assert coarse.estimated_points <= 100


def test_a_year_still_fits_the_point_budget():
    plan = _plan(span=timedelta(days=365))
    assert plan.source.name == "1h"
    assert plan.estimated_points <= 720


def test_an_inverted_window_is_rejected():
    with pytest.raises(ValueError, match="after start"):
        plan_series(NOW, NOW - timedelta(hours=1), now=NOW)


def test_an_absurd_window_is_rejected_rather_than_planned():
    with pytest.raises(WindowTooWide):
        plan_series(NOW - timedelta(days=MAX_WINDOW_DAYS + 1), NOW, now=NOW)
