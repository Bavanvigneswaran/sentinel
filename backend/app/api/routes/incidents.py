"""Incidents workspace: the fleet-wide list, a per-incident timeline of its
correlated AlertEvents, and a manual trigger to regenerate the AI summary/
root-cause outside the insights worker's own cadence.

Simple CRUD-adjacent reads live directly here, matching alerts.py's and
forecasts.py's style for operations that are genuinely just an ORM query;
the one piece of real logic (deciding whether a regeneration is actually
needed) lives in app/ai/insights_service.py, shared with the background
worker so the two can never disagree about what "regenerated" means.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.ai.client import AIClient, AIUnavailable, build_ai_client
from app.ai.insights_service import refresh_incident_insights
from app.api.deps import CurrentUser, TenantSession
from app.config import get_settings
from app.models import AlertEvent
from app.models.incidents import Incident
from app.schemas.alerts import AlertEventOut
from app.schemas.incidents import IncidentDetailOut, IncidentOut

router = APIRouter(tags=["incidents"])


def _ai_client_dependency() -> AIClient:
    try:
        return build_ai_client(get_settings())
    except AIUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI insights are not configured (no ANTHROPIC_API_KEY)",
        ) from exc


AIClientDep = Annotated[AIClient, Depends(_ai_client_dependency)]


async def _incident_or_404(session: TenantSession, incident_id: uuid.UUID) -> Incident:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return incident


async def _events_for(session: TenantSession, incident_id: uuid.UUID) -> list[AlertEvent]:
    return list(
        await session.scalars(
            sa.select(AlertEvent)
            .where(AlertEvent.incident_id == incident_id)
            .order_by(AlertEvent.fired_at)
        )
    )


def _detail_out(incident: Incident, events: list[AlertEvent]) -> IncidentDetailOut:
    return IncidentDetailOut(
        id=incident.id,
        device_id=incident.device_id,
        status=incident.status,
        opened_at=incident.opened_at,
        closed_at=incident.closed_at,
        summary_text=incident.summary_text,
        summary_model=incident.summary_model,
        summary_generated_at=incident.summary_generated_at,
        root_cause_text=incident.root_cause_text,
        root_cause_model=incident.root_cause_model,
        root_cause_generated_at=incident.root_cause_generated_at,
        events=[AlertEventOut.model_validate(e) for e in events],
    )


@router.get("/incidents", response_model=list[IncidentOut])
async def list_incidents(
    user: CurrentUser,
    session: TenantSession,
    status_filter: Literal["open", "resolved", "all"] = Query(default="all", alias="status"),
    device_id: uuid.UUID | None = None,
) -> list[Incident]:
    query = sa.select(Incident).order_by(Incident.opened_at.desc())
    if status_filter != "all":
        query = query.where(Incident.status == status_filter)
    if device_id is not None:
        query = query.where(Incident.device_id == device_id)
    rows = await session.scalars(query)
    return list(rows)


@router.get("/incidents/{incident_id}", response_model=IncidentDetailOut)
async def get_incident(
    incident_id: uuid.UUID, user: CurrentUser, session: TenantSession
) -> IncidentDetailOut:
    incident = await _incident_or_404(session, incident_id)
    events = await _events_for(session, incident_id)
    return _detail_out(incident, events)


@router.post("/incidents/{incident_id}/regenerate", response_model=IncidentDetailOut)
async def regenerate_incident_insights(
    incident_id: uuid.UUID,
    user: CurrentUser,
    session: TenantSession,
    ai_client: AIClientDep,
) -> IncidentDetailOut:
    """Bypasses insights_service's own freshness check: a user pressing
    "regenerate" wants a new answer even if nothing has technically changed
    since the last one — the one deliberate crack in the caching layer,
    open only to an explicit user action, never to the background sweep."""
    incident = await _incident_or_404(session, incident_id)
    await refresh_incident_insights(session, incident, ai_client, force=True)
    await session.commit()
    await session.refresh(incident)
    events = await _events_for(session, incident_id)
    return _detail_out(incident, events)
