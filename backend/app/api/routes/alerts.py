"""Alert rule CRUD, firing history, and silences.

Simple CRUD lives directly here rather than behind a service, matching
devices.py's style for operations that are genuinely just an ORM query.
"""

from __future__ import annotations

import uuid
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, TenantSession
from app.models import Device
from app.models.alerts import AlertEvent, AlertRule, AlertSilence, AnomalyBaseline
from app.schemas.alerts import (
    AlertEventOut,
    AlertRuleCreate,
    AlertRuleOut,
    AlertRuleUpdate,
    AlertSilenceCreate,
    AlertSilenceOut,
    AnomalyBaselineOut,
)

router = APIRouter(tags=["alerts"])

#: No pagination in V1 — a fixed cap keeps the triage query cheap and simple.
MAX_EVENTS = 200


async def _existing_device_or_404(
    session, device_id: uuid.UUID | None
) -> None:
    """RLS already scopes this, so a miss means "not yours" or "not there" —
    indistinguishable, which is what we want (mirrors devices.py's
    create_enrollment_code)."""
    if device_id is None:
        return
    exists = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Device)
        .where(Device.id == device_id, Device.deleted_at.is_(None))
    )
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")


# --- rules -------------------------------------------------------------------


@router.get("/alerts/rules", response_model=list[AlertRuleOut])
async def list_alert_rules(user: CurrentUser, session: TenantSession) -> list[AlertRule]:
    rows = await session.scalars(sa.select(AlertRule).order_by(AlertRule.created_at))
    return list(rows)


@router.post("/alerts/rules", response_model=AlertRuleOut, status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    payload: AlertRuleCreate, user: CurrentUser, session: TenantSession
) -> AlertRule:
    await _existing_device_or_404(session, payload.device_id)
    rule = AlertRule(user_id=user.id, **payload.model_dump())
    session.add(rule)
    await session.commit()
    return rule


@router.patch("/alerts/rules/{rule_id}", response_model=AlertRuleOut)
async def update_alert_rule(
    rule_id: uuid.UUID, payload: AlertRuleUpdate, user: CurrentUser, session: TenantSession
) -> AlertRule:
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    updates = payload.model_dump(exclude_unset=True)
    if "device_id" in updates:
        await _existing_device_or_404(session, updates["device_id"])
    for field, value in updates.items():
        setattr(rule, field, value)

    try:
        await session.commit()
    except sa.exc.IntegrityError as exc:
        # Most likely rule_type_fields: a PATCH that touches comparison or
        # threshold without also touching rule_type (so AlertRuleUpdate's own
        # validator had nothing to check) can still leave the row
        # inconsistent with its unchanged rule_type — e.g. setting threshold
        # on an existing anomaly rule. The DB CHECK is the backstop; this
        # turns it into a clean 422 instead of a raw 500.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="comparison/threshold must be set for a threshold rule and unset for an "
            "anomaly rule",
        ) from exc
    # `updated_at`'s onupdate is server-evaluated; without a refresh the ORM
    # object still holds its pre-update value, and touching it later during
    # response serialization (outside this coroutine's DB context) raises
    # MissingGreenlet rather than silently returning stale data.
    await session.refresh(rule)
    return rule


@router.delete("/alerts/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_rule(rule_id: uuid.UUID, user: CurrentUser, session: TenantSession) -> None:
    result = await session.execute(sa.delete(AlertRule).where(AlertRule.id == rule_id))
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    await session.commit()


# --- events (triage) -----------------------------------------------------------


@router.get("/alerts/events", response_model=list[AlertEventOut])
async def list_alert_events(
    user: CurrentUser,
    session: TenantSession,
    status_filter: Literal["firing", "resolved", "all"] = Query(default="all", alias="status"),
    device_id: uuid.UUID | None = None,
    limit: int = Query(default=MAX_EVENTS, ge=1, le=MAX_EVENTS),
) -> list[AlertEvent]:
    query = sa.select(AlertEvent).order_by(AlertEvent.fired_at.desc()).limit(limit)
    if status_filter != "all":
        query = query.where(AlertEvent.status == status_filter)
    if device_id is not None:
        query = query.where(AlertEvent.device_id == device_id)
    rows = await session.scalars(query)
    return list(rows)


# --- anomaly baselines -----------------------------------------------------


@router.get("/alerts/anomaly-baselines", response_model=list[AnomalyBaselineOut])
async def list_anomaly_baselines(
    user: CurrentUser, session: TenantSession, device_id: uuid.UUID | None = None
) -> list[AnomalyBaseline]:
    """The live EWMA/MAD state the evaluator maintains per (device, metric) —
    the Anomalies page's evidence chart draws its baseline band from this,
    since AnomalyBaseline only stores current state, not history. No
    pagination: same small-N posture as MAX_EVENTS above."""
    query = sa.select(AnomalyBaseline)
    if device_id is not None:
        query = query.where(AnomalyBaseline.device_id == device_id)
    rows = await session.scalars(query)
    return list(rows)


# --- silences ------------------------------------------------------------------


@router.get("/alerts/silences", response_model=list[AlertSilenceOut])
async def list_alert_silences(user: CurrentUser, session: TenantSession) -> list[AlertSilence]:
    rows = await session.scalars(sa.select(AlertSilence).order_by(AlertSilence.starts_at.desc()))
    return list(rows)


@router.post(
    "/alerts/silences", response_model=AlertSilenceOut, status_code=status.HTTP_201_CREATED
)
async def create_alert_silence(
    payload: AlertSilenceCreate, user: CurrentUser, session: TenantSession
) -> AlertSilence:
    await _existing_device_or_404(session, payload.device_id)
    if payload.rule_id is not None:
        exists = await session.scalar(
            sa.select(sa.func.count())
            .select_from(AlertRule)
            .where(AlertRule.id == payload.rule_id)
        )
        if not exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    silence = AlertSilence(user_id=user.id, **payload.model_dump())
    session.add(silence)
    await session.commit()
    return silence


@router.delete("/alerts/silences/{silence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_silence(
    silence_id: uuid.UUID, user: CurrentUser, session: TenantSession
) -> None:
    result = await session.execute(sa.delete(AlertSilence).where(AlertSilence.id == silence_id))
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Silence not found")
    await session.commit()
