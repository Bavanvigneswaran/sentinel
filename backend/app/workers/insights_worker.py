"""The insights worker: a periodic sweep that keeps every open incident's
summary/root-cause current.

Shape mirrors app/workers/forecast_worker.py: a periodic global task, one
unscoped enumeration query (which users currently have an open incident),
everything else through a session scoped via scope_to_user(). See that
module's docstring for the tenancy reasoning, which applies unchanged here.

This worker never decides whether an incident opens or closes — that's
app/alerts/incident_apply.py, called synchronously from the alert
evaluator's own sweep. It only asks, for each currently-open incident,
"is the cached explanation still describing the right set of events?" via
app/insights/service.py, which is also where the caching decision is made.

Unlike the other three workers, this one is not CPU-bound and has no
configuration that can be absent: since Phase 8's Claude calls were replaced
by app/insights/generator.py's local templates, a tick needs no API key and
cannot fail on the network, so the graceful-no-op-when-unconfigured branch
every other optional integration carries has no counterpart here.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from app.config import get_settings
from app.db import AdminSessionLocal, SessionLocal, scope_to_user
from app.insights.service import refresh_incident_insights
from app.models.incidents import Incident

logger = logging.getLogger(__name__)


class InsightsWorker:
    def __init__(self) -> None:
        settings = get_settings()
        self._interval_seconds = settings.insights_worker_interval_seconds

    async def run(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("insights worker tick failed")
            await asyncio.sleep(self._interval_seconds)

    async def run_once(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        for user_id in await self._users_with_open_incidents():
            try:
                await self._refresh_user(user_id, now)
            except Exception:
                # One tenant's failure must not stop the sweep for everyone else.
                logger.exception("insights refresh failed user_id=%s", user_id)

    async def _users_with_open_incidents(self) -> list[uuid.UUID]:
        """The one unscoped query in this module — see the module docstring."""
        async with AdminSessionLocal() as session:
            rows = await session.scalars(
                sa.select(Incident.user_id).where(Incident.status == "open").distinct()
            )
            return list(rows)

    async def _refresh_user(self, user_id: uuid.UUID, now: datetime) -> None:
        async with SessionLocal() as session:
            scope_to_user(session, user_id)
            incidents = list(
                await session.scalars(sa.select(Incident).where(Incident.status == "open"))
            )
            for incident in incidents:
                await refresh_incident_insights(session, incident, now=now)
            await session.commit()
