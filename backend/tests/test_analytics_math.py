"""Pure arithmetic in app/analysis/analytics.py and app/analysis/availability.py
— no DB, no I/O. app/services/report_service.py's own tests exercise the real
rollup reads that feed these; this module only has to trust the math once it
has the inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.analysis.analytics import PeriodStats, build_trend
from app.analysis.availability import compute_reliability, compute_uptime


def test_build_trend_computes_delta_percent():
    current = PeriodStats(avg=60.0, min=10.0, max=90.0)
    previous = PeriodStats(avg=50.0, min=5.0, max=80.0)
    trend = build_trend("cpu_percent", None, current, previous)
    assert trend.delta_percent == 20.0  # (60-50)/50 * 100


def test_build_trend_is_none_when_current_average_is_unmeasured():
    current = PeriodStats(avg=None, min=None, max=None)
    previous = PeriodStats(avg=50.0, min=5.0, max=80.0)
    trend = build_trend("cpu_percent", None, current, previous)
    assert trend.delta_percent is None


def test_build_trend_is_none_when_previous_average_is_unmeasured():
    current = PeriodStats(avg=60.0, min=10.0, max=90.0)
    previous = PeriodStats(avg=None, min=None, max=None)
    trend = build_trend("cpu_percent", None, current, previous)
    assert trend.delta_percent is None


def test_build_trend_avoids_dividing_by_a_zero_previous_average():
    current = PeriodStats(avg=10.0, min=1.0, max=20.0)
    previous = PeriodStats(avg=0.0, min=0.0, max=0.0)
    trend = build_trend("cpu_percent", None, current, previous)
    assert trend.delta_percent is None


def test_build_trend_carries_the_entity_through():
    trend = build_trend(
        "disk_percent",
        "/data",
        PeriodStats(avg=70.0, min=60.0, max=80.0),
        PeriodStats(avg=65.0, min=55.0, max=75.0),
    )
    assert trend.entity == "/data"


def test_compute_uptime_full_coverage():
    result = compute_uptime(sample_weight_seconds=86_400, period_seconds=86_400)
    assert result.uptime_percent == 100.0


def test_compute_uptime_partial_coverage():
    result = compute_uptime(sample_weight_seconds=21_600, period_seconds=86_400)
    assert result.uptime_percent == 25.0


def test_compute_uptime_never_reporting():
    result = compute_uptime(sample_weight_seconds=0, period_seconds=86_400)
    assert result.uptime_percent == 0.0


def test_compute_uptime_clamps_at_100_percent():
    # Bucket-boundary slop can make the rollup report very slightly more
    # covered time than the requested window — must never read as >100%.
    result = compute_uptime(sample_weight_seconds=90_000, period_seconds=86_400)
    assert result.uptime_percent == 100.0


def test_compute_uptime_zero_width_period_is_unknown():
    result = compute_uptime(sample_weight_seconds=0, period_seconds=0)
    assert result.uptime_percent is None


def test_compute_reliability_with_no_incidents():
    result = compute_reliability([], alert_fired_count=0)
    assert result.incident_count == 0
    assert result.resolved_incident_count == 0
    assert result.mean_time_to_resolve_seconds is None
    assert result.alert_fired_count == 0


def test_compute_reliability_mean_time_to_resolve_only_counts_resolved():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    windows = [
        (t0, t0 + timedelta(minutes=10)),  # resolved in 10 minutes
        (t0, t0 + timedelta(minutes=30)),  # resolved in 30 minutes
        (t0, None),  # still open — must not count toward the mean
    ]
    result = compute_reliability(windows, alert_fired_count=5)
    assert result.incident_count == 3
    assert result.resolved_incident_count == 2
    assert result.mean_time_to_resolve_seconds == timedelta(minutes=20).total_seconds()
    assert result.alert_fired_count == 5
