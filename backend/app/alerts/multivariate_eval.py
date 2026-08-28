"""The evaluator's rule_type="multivariate" path: judge a rule's comparison
against layer 4's novelty percentile rather than a measured reading, then hand
off to state_apply.apply_step_result() for the same state-machine and event
bookkeeping the other three paths use.

Split out of evaluator.py for exactly the reason anomaly_eval.py and
forecast_eval.py were: evaluator.py owns the sweep, this module owns turning
one (rule, device, score) judgment into a step() outcome. It is the shortest
of the four because the score arrives already computed — like forecast_eval,
it reads a model's output and never produces one.

The value being judged is not a measurement, and that difference is worth
holding onto. A threshold rule fires on something a machine reported; this
fires on how unusual a *combination* of reported things is, relative to a
model trained on that machine's own history. So the same score means different
absolute conditions on two different machines, which is the point — 95 means
"unusual for this one", not "95% CPU".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.state_apply import apply_step_result
from app.analysis.alerts import evaluate_condition, step
from app.models import AlertRule, AlertState, Device


async def evaluate_multivariate_pair(
    session: AsyncSession,
    user_id: uuid.UUID,
    rule: AlertRule,
    device: Device,
    state_row: AlertState | None,
    score: float | None,
    now: datetime,
) -> None:
    if state_row is None:
        state_row = AlertState(user_id=user_id, rule_id=rule.id, device_id=device.id, state="ok")
        session.add(state_row)

    assert rule.comparison is not None and rule.threshold is not None  # noqa: S101 — rule_type_fields CHECK

    # `None` — no model for this device, no fresh reading, or a reading that
    # could not fill the model's vector — is unknown, never False. step()
    # treats it as a no-op, so an already-firing rule is not auto-resolved by
    # a device going quiet or by a model being retrained away, matching
    # Phase 5's rule and anomaly_eval's treatment of a cold baseline.
    condition_met: bool | None = None
    if score is not None:
        condition_met = evaluate_condition(score, rule.comparison, rule.threshold)

    result = step(
        state=state_row.state,  # type: ignore[arg-type]
        pending_since=state_row.pending_since,
        condition_met=condition_met,
        now=now,
        for_duration_seconds=rule.for_duration_seconds,
    )

    # The score is the value: it is what was judged and what the UI should
    # show, so it goes through `value` like a threshold rule's reading rather
    # than into a separate evidence column. No new AlertEvent columns exist
    # for this rule type, deliberately — `observed_value` and the baseline
    # fields describe a *metric's* deviation, and reusing them for a joint
    # score would make two different things share a name.
    await apply_step_result(
        session,
        user_id,
        rule,
        device,
        state_row,
        result,
        score,
        now,
        comparison=rule.comparison,
        threshold=rule.threshold,
    )
