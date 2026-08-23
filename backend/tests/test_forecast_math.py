"""Pure unit tests for Holt-Winters forecasting and time-to-exhaustion. No DB,
no app — see app/analysis/forecast.py for the functions under test.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

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


# --- early forecasts, and the honesty guard that makes them safe ------------


def test_a_forecast_never_reaches_further_than_its_own_history():
    """The guard that makes an early forecast defensible rather than reckless.

    Once the worker plans its window from when a device started reporting, a
    four-minute-old device gets 10s buckets and enough points to fit. Without
    this cap it would then extrapolate a full day from four minutes.
    """
    from app.analysis.forecast import fit_holt_winters

    values = [50.0 + (i % 3) * 0.5 for i in range(24)]
    points = fit_holt_winters(values, 10)

    assert points, "24 points should be enough to fit"
    observed = 24 * 10
    assert points[-1].offset_seconds <= observed


def test_an_established_device_still_gets_the_full_24h_horizon():
    """The cap must not shorten a forecast that has earned its length."""
    from app.analysis.forecast import HORIZON_SECONDS, fit_holt_winters

    values = [50.0 + (i % 5) * 0.4 for i in range(200)]
    points = fit_holt_winters(values, 3600)

    assert points
    assert points[-1].offset_seconds == HORIZON_SECONDS


@pytest.mark.parametrize(
    ("history_seconds", "expected"),
    [
        (0, "provisional"),
        (60 * 30, "provisional"),
        (6 * 3600, "medium"),
        (24 * 3600, "medium"),
        (2 * 86_400, "high"),
        (30 * 86_400, "high"),
    ],
)
def test_confidence_reports_how_much_history_backed_the_fit(history_seconds, expected):
    from app.analysis.forecast import forecast_confidence

    assert forecast_confidence(history_seconds) == expected


def test_an_unknown_history_gets_the_cautious_answer():
    """A pre-migration row has a null history_seconds. It must not be
    presented as trustworthy just because nothing recorded otherwise."""
    from app.analysis.forecast import forecast_confidence

    assert forecast_confidence(0) == "provisional"


def test_a_brand_new_device_still_gets_its_checked_and_found_nothing_row():
    """Phase 7's invariant: the row exists with points=[] so computed_at can
    say when it last looked. Narrowing the window to a five-second-old device
    would leave no row at all, which reads as "the worker never ran"."""
    from datetime import UTC, datetime, timedelta

    from app.workers.forecast_worker import _window_start_for

    now = datetime.now(UTC)
    lookback = now - timedelta(days=14)

    class _Device:
        enrolled_at = now - timedelta(seconds=5)
        created_at = now - timedelta(seconds=5)

    assert _window_start_for(_Device(), lookback, now) == lookback


def test_a_young_devices_window_starts_when_it_started_reporting():
    """The whole fix: a flat 14-day window made plan_series choose hourly
    buckets, which found ~1 row for an hour-old device and returned no
    forecast for a full day — reading as "forecasting is broken", not
    "not yet"."""
    from datetime import UTC, datetime, timedelta

    from app.workers.forecast_worker import _window_start_for

    now = datetime.now(UTC)
    enrolled = now - timedelta(hours=1)

    class _Device:
        enrolled_at = enrolled
        created_at = enrolled

    assert _window_start_for(_Device(), now - timedelta(days=14), now) == enrolled


def test_an_established_devices_window_is_still_the_full_lookback():
    from datetime import UTC, datetime, timedelta

    from app.workers.forecast_worker import _window_start_for

    now = datetime.now(UTC)
    lookback = now - timedelta(days=14)

    class _Device:
        enrolled_at = now - timedelta(days=90)
        created_at = now - timedelta(days=90)

    assert _window_start_for(_Device(), lookback, now) == lookback
