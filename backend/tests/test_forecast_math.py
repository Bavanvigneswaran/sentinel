"""Pure unit tests for Holt-Winters forecasting and time-to-exhaustion. No DB,
no app — see app/analysis/forecast.py for the functions under test.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from app.analysis.forecast import (
    MIN_POINTS_EXHAUSTION,
    MIN_POINTS_TREND,
    estimate_time_to_exhaustion,
    fit_holt_winters,
)

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _rising_series(n: int, *, start: float = 20.0, step: float = 0.5) -> list[float]:
    return [start + step * i for i in range(n)]


def _timestamps(n: int, *, interval_seconds: int = 3600) -> list[datetime]:
    return [NOW - timedelta(seconds=interval_seconds * (n - 1 - i)) for i in range(n)]


# --- fit_holt_winters ---------------------------------------------------------


def test_too_few_points_returns_none_without_fitting():
    assert fit_holt_winters(_rising_series(MIN_POINTS_TREND - 1), bucket_seconds=3600) is None


def test_zero_bucket_seconds_returns_none():
    assert fit_holt_winters(_rising_series(MIN_POINTS_TREND), bucket_seconds=0) is None


def test_a_rising_trend_forecasts_further_increase_with_ordered_intervals():
    values = _rising_series(60)
    points = fit_holt_winters(values, bucket_seconds=3600)
    assert points is not None
    assert len(points) == 24  # 24h horizon / 3600s buckets

    offsets = [p.offset_seconds for p in points]
    assert offsets == sorted(offsets)
    assert offsets[0] == 3600

    for p in points:
        assert p.lower <= p.predicted <= p.upper
    # A clear upward trend should keep extrapolating upward, not flatten to
    # the last observed value.
    assert points[-1].predicted > values[-1]


def test_a_flat_series_does_not_crash_and_stays_flat():
    values = [10.0] * 40
    points = fit_holt_winters(values, bucket_seconds=3600)
    assert points is not None
    for p in points:
        assert math.isclose(p.predicted, 10.0, abs_tol=1.0)


# --- estimate_time_to_exhaustion ----------------------------------------------


def test_too_few_points_returns_none():
    n = MIN_POINTS_EXHAUSTION - 1
    result = estimate_time_to_exhaustion(_timestamps(n), _rising_series(n), now=NOW)
    assert result is None


def test_a_rising_series_projects_a_future_exhaustion_date():
    n = 20
    timestamps = _timestamps(n, interval_seconds=86_400)  # one point per day
    values = [50.0 + i for i in range(n)]  # +1%/day, starting at 50%
    result = estimate_time_to_exhaustion(timestamps, values, now=NOW)
    assert result is not None
    assert result.slope_per_day > 0
    assert result.projected_at is not None
    assert result.projected_at > NOW


def test_a_flat_series_never_projects_exhaustion():
    n = 20
    timestamps = _timestamps(n, interval_seconds=86_400)
    values = [50.0] * n
    result = estimate_time_to_exhaustion(timestamps, values, now=NOW)
    assert result is not None
    assert result.slope_per_day <= 0
    assert result.projected_at is None


def test_a_falling_series_never_projects_exhaustion():
    n = 20
    timestamps = _timestamps(n, interval_seconds=86_400)
    values = [90.0 - i for i in range(n)]
    result = estimate_time_to_exhaustion(timestamps, values, now=NOW)
    assert result is not None
    assert result.slope_per_day <= 0
    assert result.projected_at is None


def test_already_past_the_ceiling_projects_immediately():
    n = 20
    values = [50.0 + 3 * i for i in range(n)]  # rising, last value is 107 > 100
    timestamps = _timestamps(n, interval_seconds=86_400)
    result = estimate_time_to_exhaustion(timestamps, values, ceiling=100.0, now=NOW)
    assert result is not None
    assert result.projected_at is not None
    assert result.projected_at <= NOW
