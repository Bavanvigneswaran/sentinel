"""Live-pipeline REST surface: viewer tickets and the recent-samples primer.

Both endpoints are ordinary authenticated, RLS-scoped REST — the WebSocket
itself (app/live/viewer_ws.py) is the only part of the live pipeline that
cannot use the normal JWT dependency chain.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, TenantSession
from app.live.tickets import mint_ticket
from app.models import (
    Device,
    DiskIoSample,
    DiskUsageSample,
    LatencySample,
    MetricSample,
    NetSample,
    ProcessSample,
)
from app.schemas.live import (
    DiskIoPoint,
    DiskUsageSnapshot,
    LatencyPoint,
    NetPoint,
    ProcessSnapshot,
    RecentSamplesOut,
    SystemPoint,
    TicketOut,
)

router = APIRouter(tags=["live"])

DEFAULT_RECENT_SECONDS = 300
MAX_RECENT_SECONDS = 900
#: 900s of 1s-resolution rows is the worst case for ONE entity; also caps the
#: cost of an agent that reconnects after a long outage with a raw backlog.
#: A domain with several entities needs that budget per entity — net_samples
#: on an ordinary Mac carries six NICs, so a 300s live window is 1800 rows
#: against a 900 cap. See _series() for the other half of that fix.
MAX_ROWS_PER_SERIES = 900

#: Ceiling on entities a single multi-entity domain may contribute to the
#: primer, mirroring series_service.MAX_ENTITIES. The agent already caps
#: entities per sample at 64 (protocol.MAX_ENTITIES_PER_SAMPLE); this is the
#: read-side budget, chosen so a pathological device cannot turn one primer
#: into a 60k-row response.
MAX_ENTITIES_PER_DOMAIN = 24

#: Columns every sample table carries that are not part of the wire shape —
#: device_id/user_id are implicit in the request, resolution_seconds is an
#: ingest-time bookkeeping detail the frontend has no use for here.
_EXCLUDED_COLUMNS = {"device_id", "user_id", "resolution_seconds"}


@router.post("/ws/tickets", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
async def create_ticket(user: CurrentUser) -> TicketOut:
    """Mint a single-use, 30s ticket for the viewer WebSocket.

    A browser cannot set an Authorization header on a WS handshake, and the
    15-minute access JWT has no business appearing in a query string — see
    app/live/tickets.py.
    """
    ticket = await mint_ticket(user.id)
    return TicketOut(ticket=ticket)


def _row_to_point(row, point_cls):  # noqa: ANN001
    data = {
        col.key: getattr(row, col.key)
        for col in sa.inspect(type(row)).mapper.column_attrs
        if col.key not in _EXCLUDED_COLUMNS
    }
    return point_cls(**data)


async def _series(
    session,
    model,
    point_cls,
    *,
    device_id: uuid.UUID,
    since: datetime,
    multi_entity: bool = False,
) -> list:
    """The window's rows, oldest first, truncated from the FRONT if it has to
    truncate at all.

    Ordering descending and reversing — rather than the obvious ascending
    LIMIT — is what makes the cap cut the right end. This primes a chart that
    is about to continue in real time, so the rows that matter are the ones
    nearest to now; an ascending LIMIT keeps the oldest and silently drops
    exactly the part the live stream is about to join onto. The bug only shows
    on a multi-entity domain, where the row count is (seconds x entities)
    rather than seconds: six NICs at 1s over the default 300s window is 1800
    rows against a 900 cap, so the network chart opened with a trace that
    ended two and a half minutes in the past and then jumped when the socket
    delivered `now`.
    """
    budget = MAX_ROWS_PER_SERIES * (MAX_ENTITIES_PER_DOMAIN if multi_entity else 1)
    rows = (
        await session.execute(
            sa.select(model)
            .where(model.device_id == device_id, model.ts >= since)
            .order_by(model.ts.desc())
            .limit(budget)
        )
    ).scalars().all()
    return [_row_to_point(r, point_cls) for r in reversed(rows)]


async def _latest_snapshot(
    session, model, point_cls, *, device_id: uuid.UUID, since: datetime
) -> list:
    """All rows at the most recent timestamp within the window — a full
    snapshot of a multi-entity domain (mounts, ranked processes), not a
    series. Empty if nothing has landed within `since`."""
    latest_ts = await session.scalar(
        sa.select(sa.func.max(model.ts)).where(model.device_id == device_id, model.ts >= since)
    )
    if latest_ts is None:
        return []
    rows = (
        await session.execute(
            sa.select(model).where(model.device_id == device_id, model.ts == latest_ts)
        )
    ).scalars().all()
    return [_row_to_point(r, point_cls) for r in rows]


@router.get("/devices/{device_id}/samples/recent", response_model=RecentSamplesOut)
async def recent_samples(
    device_id: uuid.UUID,
    user: CurrentUser,
    session: TenantSession,
    seconds: int = Query(default=DEFAULT_RECENT_SECONDS, ge=1, le=MAX_RECENT_SECONDS),
) -> RecentSamplesOut:
    """Prime a freshly-opened Live Monitoring chart.

    The agent's live-mode upshift takes up to ~one push interval to land
    (see app/live/supervisor.py) — without this, the chart is blank for that
    window. A stopgap: Phase 4's time-range query API with continuous
    aggregates supersedes it for anything beyond a few minutes of history.
    """
    exists = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Device)
        .where(Device.id == device_id, Device.deleted_at.is_(None))
    )
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    since = datetime.now(UTC) - timedelta(seconds=seconds)

    return RecentSamplesOut(
        device_id=device_id,
        since=since,
        system=await _series(session, MetricSample, SystemPoint, device_id=device_id, since=since),
        disk_io=await _series(
            session, DiskIoSample, DiskIoPoint,
            device_id=device_id, since=since, multi_entity=True,
        ),
        net=await _series(
            session, NetSample, NetPoint,
            device_id=device_id, since=since, multi_entity=True,
        ),
        latency=await _series(
            session, LatencySample, LatencyPoint,
            device_id=device_id, since=since, multi_entity=True,
        ),
        disk_usage=await _latest_snapshot(
            session, DiskUsageSample, DiskUsageSnapshot, device_id=device_id, since=since
        ),
        processes=await _latest_snapshot(
            session, ProcessSample, ProcessSnapshot, device_id=device_id, since=since
        ),
    )
