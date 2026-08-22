"""The health score.

Pure arithmetic over app/analysis/health.py. The rules that matter are not the
exact curve values — those are a judgement call and will be tuned — but the
three that come straight out of CLAUDE.md: an unmeasured component is excluded
rather than assumed, an offline device has no score at all, and the score never
invents a number for a machine that reported nothing.
"""

from __future__ import annotations

from app.analysis.health import (
    CPU_CURVE,
    HealthInputs,
    compute_health,
    score_on_curve,
    unknown_health,
)


def test_a_quiet_machine_scores_healthy():
    result = compute_health(
        HealthInputs(
            cpu_percent=6.0,
            memory_percent=40.0,
            swap_percent=0.0,
            disk_percent=30.0,
            packet_loss_percent=0.0,
            cpu_iowait_percent=1.0,
        )
    )
    assert result.band == "healthy"
    assert result.score is not None and result.score > 90
    assert result.unavailable == ()


def test_a_saturated_machine_scores_critical():
    result = compute_health(
        HealthInputs(
            cpu_percent=99.0,
            memory_percent=97.0,
            swap_percent=90.0,
            disk_percent=99.0,
            packet_loss_percent=40.0,
        )
    )
    assert result.band == "critical"
    assert result.score is not None and result.score < 30


def test_an_unmeasured_component_is_excluded_not_scored_as_perfect():
    """A VM with no swap reading must land on the same score as an identical
    machine whose swap is genuinely idle — not a better one, and not a worse
    one. Excluding it and renormalising the weights is what makes machines on
    different platforms comparable at all."""
    measured = HealthInputs(cpu_percent=50.0, memory_percent=50.0, disk_percent=50.0)
    result = compute_health(measured)

    assert "swap" in result.unavailable
    assert "network" in result.unavailable
    # Same three readings, scored the same way, whatever else is missing.
    assert result.score == compute_health(measured).score

    swap_component = next(c for c in result.components if c.key == "swap")
    assert swap_component.value is None
    assert swap_component.score is None


def test_a_device_with_nothing_measurable_has_no_score():
    result = compute_health(HealthInputs())
    assert result.score is None
    assert result.band == "unknown"
    assert result.reason


def test_an_offline_device_is_unknown_rather_than_zero():
    result = unknown_health("device is offline")
    assert result.score is None
    assert result.band == "unknown"
    assert result.reason == "device is offline"


def test_disk_outweighs_cpu():
    """A full disk takes the machine down; a busy CPU makes it slow. The score
    has to rank those differently or it is just an average of percentages."""
    busy_cpu = compute_health(
        HealthInputs(cpu_percent=98.0, memory_percent=40.0, disk_percent=40.0)
    )
    full_disk = compute_health(
        HealthInputs(cpu_percent=40.0, memory_percent=40.0, disk_percent=98.0)
    )
    assert full_disk.score < busy_cpu.score


def test_the_curve_is_clamped_and_monotonic():
    assert score_on_curve(-5, CPU_CURVE) == 100
    assert score_on_curve(1000, CPU_CURVE) == 0
    scores = [score_on_curve(v, CPU_CURVE) for v in range(0, 101, 5)]
    assert scores == sorted(scores, reverse=True)
