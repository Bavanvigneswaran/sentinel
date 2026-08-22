"""The evaluator's rule_type="forecast" path: judge a rule's comparison against
the device's stored 24h-ahead MetricForecast rather than a live reading, then
hand off to state_apply.apply_step_result() for the same state-machine/event
bookkeeping the threshold and anomaly paths use.

Split out of evaluator.py for the same reason anomaly_eval.py was: evaluator.py
owns the sweep, this module owns turning one (rule, device, forecast) judgment
into a step() outcome. Unlike anomaly_eval.py, this module does not write to
MetricForecast — app/workers/forecast_worker.py is the only writer, on its own
slower cadence. The evaluator sweep just reads whatever the worker last
computed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.state_apply import apply_step_result
from app.analysis.alerts import evaluate_condition, step
from app.models import AlertRule, AlertState, Device, MetricForecast


async def evaluate_forecast_pair(
    session: AsyncSession,
    user_id: uuid.UUID,
    rule: AlertRule,
    device: Device,
    state_row: AlertState | None,
    forecast_row: MetricForecast | None,
    value: float | None,
    now: datetime,
) -> None:
    if state_row is None:
        state_row = AlertState(user_id=user_id, rule_id=rule.id, device_id=device.id, state="ok")
        session.add(state_row)

    assert rule.comparison is not None and rule.threshold is not None  # noqa: S101 — rule_type_fields CHECK

    # A forecast rule only ever fires while the device has a fresh live
    # reading — the same "never make a claim about a machine nobody is
    # talking to" reasoning app/analysis/health.py's unknown_health() and
    # Phase 5's "an already-firing alert is never auto-resolved by a device
    # going quiet" both follow. Without this gate, a stale MetricForecast row
    # computed while the device was still reporting could keep firing long
    # after it went offline.
    condition_met: bool | None = None
    breach_offset_seconds: int | None = None
    breach_predicted: float | None = None
    if value is not None and forecast_row is not None and forecast_row.points:
        condition_met = False
        for point in forecast_row.points:
            if evaluate_condition(point["predicted"], rule.comparison, rule.threshold):
                condition_met = True
                breach_offset_seconds = point["offset_seconds"]
                breach_predicted = point["predicted"]
                break

    result = step(
        state=state_row.state,  # type: ignore[arg-type]
        pending_since=state_row.pending_since,
        condition_met=condition_met,
        now=now,
        for_duration_seconds=rule.for_duration_seconds,
    )
    evidence = None
    if result.fire:
        assert (  # noqa: S101 — step() only fires when condition_met is True
            forecast_row is not None and breach_offset_seconds is not None
        )
        evidence = {
            "predicted_breach_at": forecast_row.computed_at
            + timedelta(seconds=breach_offset_seconds),
            "predicted_value": breach_predicted,
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
        comparison=rule.comparison,
        threshold=rule.threshold,
        evidence=evidence,
    )
