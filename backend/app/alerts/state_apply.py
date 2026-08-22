"""Applying one analysis/alerts.py step() outcome to an AlertState row and,
where it implies one, opening/closing the AlertEvent and dispatching a
best-effort notification.

Split out of evaluator.py so both the threshold path (evaluator.py's
`_evaluate_pair`) and the anomaly path (anomaly_eval.py's
`evaluate_anomaly_pair`) go through the exact same bookkeeping — the two
rule_type paths differ only in how they arrive at `condition_met` and what
evidence (if any) accompanies a fire, never in what happens once step() has
decided.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import notify
from app.analysis.alerts import StepResult
from app.models import AlertEvent, AlertRule, AlertSilence, AlertState, Device

logger = logging.getLogger(__name__)


async def apply_step_result(
    session: AsyncSession,
    user_id: uuid.UUID,
    rule: AlertRule,
    device: Device,
    state_row: AlertState,
    result: StepResult,
    value: float | None,
    now: datetime,
    *,
    comparison: str | None,
    threshold: float | None,
    evidence: dict[str, float | None] | None = None,
) -> None:
    state_row.state = result.state
    state_row.pending_since = result.pending_since
    state_row.last_evaluated_at = now
    if value is not None:
        state_row.last_value = value

    if result.fire:
        assert value is not None  # noqa: S101 — step() only fires when condition_met is True
        event = AlertEvent(
            user_id=user_id,
            rule_id=rule.id,
            device_id=device.id,
            rule_name=rule.name,
            metric=rule.metric,
            comparison=comparison,
            threshold=threshold,
            status="firing",
            value_at_fire=value,
            last_value=value,
            fired_at=now,
            **(evidence or {}),
        )
        session.add(event)
        await session.flush()  # assigns event.id
        state_row.current_event_id = event.id
        await _maybe_notify(session, user_id, rule, device, event, now, resolved=False)

    elif result.resolve and state_row.current_event_id is not None:
        event = await session.get(AlertEvent, state_row.current_event_id)
        if event is not None:
            event.status = "resolved"
            event.resolved_at = now
            event.resolved_value = value
            if value is not None:
                event.last_value = value
            await _maybe_notify(session, user_id, rule, device, event, now, resolved=True)
        state_row.current_event_id = None

    elif (
        state_row.state == "firing"
        and state_row.current_event_id is not None
        and value is not None
    ):
        # Still firing and re-evaluated: keep the open event's last-seen
        # value current without touching notified_at again.
        event = await session.get(AlertEvent, state_row.current_event_id)
        if event is not None:
            event.last_value = value


async def _maybe_notify(
    session: AsyncSession,
    user_id: uuid.UUID,
    rule: AlertRule,
    device: Device,
    event: AlertEvent,
    now: datetime,
    *,
    resolved: bool,
) -> None:
    if await _is_silenced(session, user_id, rule.id, device.id, now):
        # Leave notified_at None: "fired but never paged" stays distinct
        # from "paged, and every channel happened to fail".
        return
    try:
        if resolved:
            await notify.notify_resolved(session, user_id, event, device.name)
        else:
            await notify.notify_firing(session, user_id, event, device.name)
    except Exception:
        logger.exception(
            "notification dispatch failed rule_id=%s device_id=%s resolved=%s",
            rule.id,
            device.id,
            resolved,
        )
    event.notified_at = now


async def _is_silenced(
    session: AsyncSession,
    user_id: uuid.UUID,
    rule_id: uuid.UUID,
    device_id: uuid.UUID,
    now: datetime,
) -> bool:
    count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(AlertSilence)
        .where(
            AlertSilence.user_id == user_id,
            sa.or_(AlertSilence.device_id.is_(None), AlertSilence.device_id == device_id),
            sa.or_(AlertSilence.rule_id.is_(None), AlertSilence.rule_id == rule_id),
            AlertSilence.starts_at <= now,
            AlertSilence.ends_at >= now,
        )
    )
    return bool(count)
