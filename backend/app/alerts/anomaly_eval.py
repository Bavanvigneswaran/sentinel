"""The evaluator's rule_type="anomaly" path: read-judge-update an
AnomalyBaseline row against one fresh value, then hand off to
state_apply.apply_step_result() for the same state-machine/event bookkeeping
the threshold path uses.

Split out of evaluator.py to keep that file under the ~400-line convention —
evaluator.py owns the sweep (which rules, which devices, which values),
this module owns turning one (rule, device, value) anomaly evaluation into
a step() outcome.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.state_apply import apply_step_result
from app.analysis.alerts import step
from app.analysis.anomaly import (
    WARMUP_SAMPLES,
    BaselineState,
    Sensitivity,
    is_anomalous,
    update_baseline,
    z_score,
)
from app.models import AlertRule, AlertState, AnomalyBaseline, Device


async def evaluate_anomaly_pair(
    session: AsyncSession,
    user_id: uuid.UUID,
    rule: AlertRule,
    device: Device,
    state_row: AlertState | None,
    baseline_row: AnomalyBaseline | None,
    value: float | None,
    sensitivity: Sensitivity,
    now: datetime,
) -> AnomalyBaseline | None:
    if state_row is None:
        state_row = AlertState(user_id=user_id, rule_id=rule.id, device_id=device.id, state="ok")
        session.add(state_row)

    prior = (
        BaselineState(
            mean=baseline_row.mean, mad=baseline_row.mad, sample_count=baseline_row.sample_count
        )
        if baseline_row is not None
        else None
    )

    # Judge against the baseline as it stood before this tick's value, then
    # fold the value in regardless of the verdict — updating first would let
    # an outlier dampen its own z-score, worst exactly when it matters (a
    # spike or step change). See analysis/anomaly.py's module docstring.
    condition_met: bool | None = None
    z: float | None = None
    if value is not None:
        if prior is not None and prior.sample_count >= WARMUP_SAMPLES:
            z = z_score(value, prior)
            condition_met = is_anomalous(z, sensitivity)
        new_state = update_baseline(prior, value)
        if baseline_row is None:
            baseline_row = AnomalyBaseline(
                user_id=user_id,
                device_id=device.id,
                metric=rule.metric,
                mean=new_state.mean,
                mad=new_state.mad,
                sample_count=new_state.sample_count,
            )
            session.add(baseline_row)
        else:
            baseline_row.mean = new_state.mean
            baseline_row.mad = new_state.mad
            baseline_row.sample_count = new_state.sample_count

    result = step(
        state=state_row.state,  # type: ignore[arg-type]
        pending_since=state_row.pending_since,
        condition_met=condition_met,
        now=now,
        for_duration_seconds=rule.for_duration_seconds,
    )
    evidence = None
    if result.fire:
        assert prior is not None and z is not None and value is not None  # noqa: S101
        evidence = {
            "observed_value": value,
            "baseline_mean": prior.mean,
            "baseline_mad": prior.mad,
            "z_score": z,
        }
    await apply_step_result(
        session,
        user_id,
        rule,
        device,
        state_row,
        result,
        value,
        now,
        comparison=None,
        threshold=None,
        evidence=evidence,
    )
    return baseline_row
