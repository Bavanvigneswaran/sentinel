"""Pure unit tests for the OK/PENDING/FIRING state machine. No DB, no app —
see app/analysis/alerts.py for the function under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.analysis.alerts import evaluate_condition, step

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_evaluate_condition_covers_every_comparison():
    assert evaluate_condition(10, ">", 5) is True
    assert evaluate_condition(5, ">", 5) is False
    assert evaluate_condition(5, ">=", 5) is True
    assert evaluate_condition(4, "<", 5) is True
    assert evaluate_condition(5, "<=", 5) is True
    assert evaluate_condition(5, "==", 5) is True
    assert evaluate_condition(5, "==", 6) is False


def test_ok_to_pending_on_condition_met():
    result = step(
        state="ok", pending_since=None, condition_met=True, now=T0, for_duration_seconds=60
    )
    assert result.state == "pending"
    assert result.pending_since == T0
    assert result.fire is False
    assert result.resolve is False


def test_pending_stays_pending_before_duration_elapses():
    result = step(
        state="pending",
        pending_since=T0,
        condition_met=True,
        now=T0 + timedelta(seconds=59),
        for_duration_seconds=60,
    )
    assert result.state == "pending"
    assert result.pending_since == T0
    assert result.fire is False


def test_pending_fires_exactly_at_the_duration_boundary():
    result = step(
        state="pending",
        pending_since=T0,
        condition_met=True,
        now=T0 + timedelta(seconds=60),
        for_duration_seconds=60,
    )
    assert result.state == "firing"
    assert result.fire is True
    assert result.resolve is False


def test_pending_fires_after_the_duration_boundary():
    result = step(
        state="pending",
        pending_since=T0,
        condition_met=True,
        now=T0 + timedelta(seconds=120),
        for_duration_seconds=60,
    )
    assert result.state == "firing"
    assert result.fire is True


def test_zero_duration_never_fires_on_the_same_tick_it_entered_pending():
    """A rule can never fire on a single noisy sample, even with
    for_duration_seconds=0 — OK->PENDING and PENDING->FIRING are always two
    separate ticks."""
    entering_pending = step(
        state="ok", pending_since=None, condition_met=True, now=T0, for_duration_seconds=0
    )
    assert entering_pending.state == "pending"
    assert entering_pending.fire is False

    next_tick = step(
        state="pending",
        pending_since=entering_pending.pending_since,
        condition_met=True,
        now=T0 + timedelta(seconds=1),
        for_duration_seconds=0,
    )
    assert next_tick.state == "firing"
    assert next_tick.fire is True


def test_pending_clears_to_ok_if_condition_drops_before_firing():
    result = step(
        state="pending",
        pending_since=T0,
        condition_met=False,
        now=T0 + timedelta(seconds=10),
        for_duration_seconds=60,
    )
    assert result.state == "ok"
    assert result.pending_since is None
    # No event was ever opened, so there is nothing to resolve.
    assert result.resolve is False
    assert result.fire is False


def test_firing_stays_firing_and_never_refires_dedup():
    for _ in range(5):
        result = step(
            state="firing",
            pending_since=T0,
            condition_met=True,
            now=T0 + timedelta(minutes=5),
            for_duration_seconds=60,
        )
        assert result.state == "firing"
        assert result.fire is False
        assert result.resolve is False


def test_firing_resolves_when_condition_clears():
    result = step(
        state="firing",
        pending_since=T0,
        condition_met=False,
        now=T0 + timedelta(minutes=5),
        for_duration_seconds=60,
    )
    assert result.state == "ok"
    assert result.pending_since is None
    assert result.resolve is True
    assert result.fire is False


def test_ok_stays_ok_when_condition_not_met():
    result = step(
        state="ok", pending_since=None, condition_met=False, now=T0, for_duration_seconds=60
    )
    assert result.state == "ok"
    assert result.fire is False
    assert result.resolve is False


def test_no_fresh_sample_leaves_every_state_untouched():
    for state, pending_since in (
        ("ok", None),
        ("pending", T0),
        ("firing", T0),
    ):
        result = step(
            state=state,
            pending_since=pending_since,
            condition_met=None,
            now=T0 + timedelta(hours=1),
            for_duration_seconds=60,
        )
        assert result.state == state
        assert result.pending_since == pending_since
        assert result.fire is False
        assert result.resolve is False
