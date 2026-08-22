"""Pure logic for incident correlation and AI-response caching — no I/O,
matching analysis/alerts.py's step() in spirit: the part of the behaviour
worth unit-testing without a database.

Two small pieces:

* `next_incident_status()` — given how many of an incident's attached events
  are still firing, is the incident still open? All the branching
  app/alerts/incident_apply.py needs, factored out so it's testable without a
  session.
* `correlation_fingerprint()` — a stable hash over an incident's *event
  membership* (which events, and whether each is firing or resolved), used
  as the cache key for the AI-generated summary/root-cause. Deliberately
  excludes anything that drifts every tick regardless of the incident itself
  changing (a health score, a live metric reading) — including those would
  turn "response caching" into "cache that never hits."
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Literal, NamedTuple

IncidentStatus = Literal["open", "resolved"]


def next_incident_status(*, firing_event_count: int) -> IncidentStatus:
    """An incident stays open as long as at least one of its attached events
    is still firing."""
    return "open" if firing_event_count > 0 else "resolved"


class EventMembership(NamedTuple):
    """The part of one AlertEvent that changes what an incident's AI
    explanation should say. Anything else about the event (its value_at_fire,
    its evidence fields) is genuinely part of what the explanation describes,
    but doesn't need its own hash input — it's read fresh from the bundle
    every time the hash says a regeneration is due."""

    event_id: uuid.UUID
    status: str
    resolved_at: datetime | None


def correlation_fingerprint(events: list[EventMembership]) -> str:
    """A stable hex digest identifying "this exact set of events, in this
    exact set of states." Order-independent, since event attachment order
    carries no meaning here."""
    parts = sorted(
        f"{e.event_id}:{e.status}:{e.resolved_at.isoformat() if e.resolved_at else ''}"
        for e in events
    )
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return digest
