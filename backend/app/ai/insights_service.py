"""Orchestrates one incident's AI summary/root-cause: decide whether either
is stale, build the signal bundle only if so, call Claude, and cache the
result on the Incident row.

The single function here (`refresh_incident_insights`) is called from two
places — app/workers/insights_worker.py's periodic sweep, and the manual
POST /incidents/{id}/regenerate route — the same "one function owns the
bookkeeping" shape app/alerts/state_apply.py's apply_step_result() and
app/services/metrics_read.py's worst_entity_per_device() already use, so the
two callers can never disagree about what "regenerated" means.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient
from app.ai.prompts import SYSTEM_PROMPT, build_root_cause_prompt, build_summary_prompt
from app.analysis.incidents import correlation_fingerprint
from app.config import get_settings
from app.models.incidents import Incident
from app.services.signal_bundle import build_signal_bundle, fetch_event_membership

logger = logging.getLogger(__name__)


async def refresh_incident_insights(
    session: AsyncSession,
    incident: Incident,
    ai_client: AIClient,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> bool:
    """Regenerates whichever of the incident's summary/root-cause is stale
    (or both, when `force`), returning whether anything changed.

    "Stale" means the fingerprint over the incident's current event
    membership (analysis/incidents.py's correlation_fingerprint) differs
    from the one the cached text was generated from. A tick that finds
    nothing changed makes zero API calls — this check, not an HTTP cache, is
    the entire caching mechanism the roadmap asks for.
    """
    now = now or datetime.now(UTC)
    settings = get_settings()
    membership = await fetch_event_membership(session, incident.id)
    fingerprint = correlation_fingerprint(membership)

    summary_stale = force or incident.summary_signal_hash != fingerprint
    root_cause_stale = force or incident.root_cause_signal_hash != fingerprint
    if not summary_stale and not root_cause_stale:
        return False

    bundle = await build_signal_bundle(session, incident)
    changed = False

    if summary_stale:
        try:
            text = await ai_client.summarize(
                system=SYSTEM_PROMPT, prompt=build_summary_prompt(bundle)
            )
        except Exception:
            logger.exception("incident summary generation failed incident_id=%s", incident.id)
        else:
            incident.summary_text = text
            incident.summary_model = settings.anthropic_haiku_model
            incident.summary_generated_at = now
            incident.summary_signal_hash = fingerprint
            changed = True

    if root_cause_stale:
        try:
            text = await ai_client.analyze_root_cause(
                system=SYSTEM_PROMPT, prompt=build_root_cause_prompt(bundle)
            )
        except Exception:
            logger.exception("incident root-cause generation failed incident_id=%s", incident.id)
        else:
            incident.root_cause_text = text
            incident.root_cause_model = settings.anthropic_sonnet_model
            incident.root_cause_generated_at = now
            incident.root_cause_signal_hash = fingerprint
            changed = True

    return changed
