"""Opens, attaches to, and closes Incidents alongside AlertEvent bookkeeping.

Called from state_apply.py's apply_step_result() at exactly the two points
an AlertEvent's own lifecycle changes — this module never decides *whether*
an event fires or resolves, only what that implies about the device's
incident. Device-scoped rather than rule-scoped: two different rules firing
on the same machine at once are correlated into the same incident, which is
the whole point of an "incidents workspace" grouping alerts by what they're
likely to share a cause with.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.incidents import next_incident_status
from app.models import AlertEvent, Device, Incident


async def attach_to_incident(
    session: AsyncSession,
    user_id: uuid.UUID,
    device: Device,
    event: AlertEvent,
    now: datetime,
) -> None:
    """Called when `event` has just fired. Attaches it to the device's open
    incident, opening one first if none exists.

    The partial unique index on `incidents (device_id) WHERE status='open'`
    is the actual race guard; this read-then-maybe-insert is what makes the
    common case (an incident already open) cost one query instead of a
    failed insert plus a retry.
    """
    incident = await session.scalar(
        sa.select(Incident).where(Incident.device_id == device.id, Incident.status == "open")
    )
    if incident is None:
        incident = Incident(
            user_id=user_id,
            device_id=device.id,
            status="open",
            opened_at=now,
        )
        session.add(incident)
        await session.flush()  # assigns incident.id
    event.incident_id = incident.id


async def maybe_close_incident(session: AsyncSession, event: AlertEvent, now: datetime) -> None:
    """Called when `event` has just resolved. Closes its incident if that
    was the last of its attached events still firing."""
    if event.incident_id is None:
        return
    incident = await session.get(Incident, event.incident_id)
    if incident is None or incident.status != "open":
        return

    # SessionLocal is autoflush=False, and the caller has just set this same
    # event's status to "resolved" on the in-memory object without flushing
    # it — without this, the count below would still see the pre-update
    # "firing" status from the database and never close the incident.
    await session.flush()
    firing_count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(AlertEvent)
        .where(AlertEvent.incident_id == incident.id, AlertEvent.status == "firing")
    )
    if next_incident_status(firing_event_count=firing_count) == "resolved":
        incident.status = "resolved"
        incident.closed_at = now
