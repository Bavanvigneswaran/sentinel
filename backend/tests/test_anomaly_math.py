"""Pure unit tests for the EWMA/MAD baseline and z-score/sensitivity math. No
DB, no app — see app/analysis/anomaly.py for the functions under test.
"""

from __future__ import annotations

from app.analysis.anomaly import (
    MIN_SPREAD,
    WARMUP_SAMPLES,
    BaselineState,
    classify_severity,
    cutoff_for_sensitivity,
    is_anomalous,
    scaled_spread,
    update_baseline,
    z_score,
)


def test_first_sample_seeds_the_baseline_at_itself():
    state = update_baseline(None, 42.0)
    assert state.mean == 42.0
    assert state.mad == 0.0
    assert state.sample_count == 1


def test_identical_values_converge_mad_toward_zero_and_leave_mean_unchanged():
    state = None
    for _ in range(30):
        state = update_baseline(state, 20.0)
    assert state.mean == 20.0
    assert state.mad == 0.0
    assert state.sample_count == 30


def test_a_step_change_pulls_the_mean_geometrically_at_the_alpha_rate():
    state = BaselineState(mean=20.0, mad=0.0, sample_count=WARMUP_SAMPLES)
    first = update_baseline(state, 100.0, alpha=0.1)
    # mean moves exactly alpha of the way toward the new value on one tick.
    assert first.mean == 20.0 + 0.1 * (100.0 - 20.0)
    # ...and continues to close the gap, never overshooting or jumping.
    second = update_baseline(first, 100.0, alpha=0.1)
    assert first.mean < second.mean < 100.0


def test_z_score_is_zero_at_the_mean():
    state = BaselineState(mean=50.0, mad=2.0, sample_count=WARMUP_SAMPLES)
    assert z_score(50.0, state) == 0.0


def test_min_spread_floor_prevents_division_by_zero_on_a_flat_metric():
    # A perfectly flat metric (e.g. swap_percent pinned at 0 with no swap)
    # converges mad to exactly 0 — z_score must stay finite, not raise or
    # return inf, by falling back to MIN_SPREAD.
    state = BaselineState(mean=0.0, mad=0.0, sample_count=WARMUP_SAMPLES)
    assert scaled_spread(state) == MIN_SPREAD
    z = z_score(0.5, state)
    assert z == 0.5 / MIN_SPREAD


def test_cutoff_for_sensitivity_orders_low_medium_high_from_loose_to_tight():
    low = cutoff_for_sensitivity("low")
    medium = cutoff_for_sensitivity("medium")
    high = cutoff_for_sensitivity("high")
    assert low > medium > high


def test_is_anomalous_boundary_is_inclusive():
    cutoff = cutoff_for_sensitivity("medium")
    assert is_anomalous(cutoff, "medium") is True
    assert is_anomalous(cutoff - 0.01, "medium") is False
    # Symmetric: a large negative deviation is anomalous too.
    assert is_anomalous(-cutoff, "medium") is True


def test_classify_severity_boundaries():
    assert classify_severity(3.9) == "watch"
    assert classify_severity(4.0) == "warning"
    assert classify_severity(5.9) == "warning"
    assert classify_severity(6.0) == "critical"
    # Magnitude, not sign.
    assert classify_severity(-7.0) == "critical"
