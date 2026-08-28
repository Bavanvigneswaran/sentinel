"""Orchestrates one incident's summary/root-cause: decide whether either is
stale, build the signal bundle only if so, generate the text, and cache the
result on the Incident row.

Moved from app/ai/insights_service.py when generation stopped calling a
hosted model. The orchestration is unchanged — the caching decision, the
fingerprint, and the two callers are all exactly as Phase 8 built them; what
changed underneath is that `generator` is now a pure local template pass
(app/insights/generator.py) rather than an API client.

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
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.incidents import correlation_fingerprint
from app.insights.generator import GENERATOR_ID, TemplateInsightGenerator
from app.models.incidents import Incident
from app.services.signal_bundle import SignalBundle, build_signal_bundle, fetch_event_membership

logger = logging.getLogger(__name__)


class InsightGenerator(Protocol):
    """The seam app/ai/client.py's AIClient used to be.

    Kept after the switch to templates for one forward-looking reason: the
    project's constraint is no *hosted* model, not no model at all, so a
    locally-run one could be substituted here without the service, the
    worker or the route changing. It takes the SignalBundle directly rather
    than a pair of prompt strings — a template pass wants the structured
    data, and building prose prompts to hand to a local function would be a
    detour through a format only an LLM needed.
    """

    def summarize(self, bundle: SignalBundle, *, now: datetime) -> str: ...

    def explain(self, bundle: SignalBundle, *, now: datetime) -> str: ...


#: Stateless and construction cannot fail, so one shared instance is enough —
#: unlike AnthropicAIClient, which held an API key and a session.
DEFAULT_GENERATOR: InsightGenerator = TemplateInsightGenerator()


async def refresh_incident_insights(
    session: AsyncSession,
    incident: Incident,
    generator: InsightGenerator | None = None,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> bool:
    """Regenerates whichever of the incident's summary/root-cause is stale
    (or both, when `force`), returning whether anything changed.

    "Stale" means the fingerprint over the incident's current event
    membership (analysis/incidents.py's correlation_fingerprint) differs
    from the one the cached text was generated from.

    That check mattered more when it was gating a paid API call, but it is
    kept for a reason that outlives the cost: `summary_generated_at` should
    say when this explanation was actually *reached*, not when a sweep last
    happened to run over it. Regenerating identical text every 60 seconds
    would make that column meaningless.
    """
    now = now or datetime.now(UTC)
    generator = generator or DEFAULT_GENERATOR
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
            text = generator.summarize(bundle, now=now)
        except Exception:
            # Still swallowed, but guarding something different now: not a
            # network failure or a rate limit, but a bug in a template. The
            # reason to keep swallowing is the sweep — one incident whose
            # bundle hits a bad branch must not stop the remaining incidents
            # for this tenant. The hash is left as it was, so the next tick
            # retries rather than treating the failure as handled.
            logger.exception("incident summary generation failed incident_id=%s", incident.id)
        else:
            incident.summary_text = text
            incident.summary_model = GENERATOR_ID
            incident.summary_generated_at = now
            incident.summary_signal_hash = fingerprint
            changed = True

    if root_cause_stale:
        try:
            text = generator.explain(bundle, now=now)
        except Exception:
            logger.exception("incident root-cause generation failed incident_id=%s", incident.id)
        else:
            incident.root_cause_text = text
            incident.root_cause_model = GENERATOR_ID
            incident.root_cause_generated_at = now
            incident.root_cause_signal_hash = fingerprint
            changed = True

    return changed
