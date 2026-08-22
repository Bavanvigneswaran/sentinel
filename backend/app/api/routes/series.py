"""The time-range query API.

`GET /devices/{id}/series` answers "show me this window", not "read me this
table". The caller states a window and a point budget; the server picks raw or a
1m/5m/1h rollup, buckets it to fit, and reports in the response which source and
bucket width it used — see app/services/series_service.py for the choice, and
app/models/rollups.py for why the sources are interchangeable.

The `domains` parameter is a closed enum, never a table name. It is mapped to a
rollup spec object by identity before any SQL is built, so a request cannot
name an object the query builder was not designed for.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, TenantSession
from app.models import Device
from app.models.rollups import DOMAINS_BY_SOURCE
from app.schemas.series import (
    DOMAIN_SOURCES,
    POINT_MODELS,
    DomainName,
    SeriesOut,
)
from app.services.series_service import (
    DEFAULT_MAX_POINTS,
    MAX_MAX_POINTS,
    MAX_WINDOW_DAYS,
    MIN_MAX_POINTS,
    WindowTooWide,
    fetch_series,
    plan_series,
)

router = APIRouter(tags=["series"])

DEFAULT_RANGE_SECONDS = 3600
MAX_RANGE_SECONDS = MAX_WINDOW_DAYS * 86_400

DEFAULT_DOMAINS: list[DomainName] = ["system"]


@router.get("/devices/{device_id}/series", response_model=SeriesOut)
async def device_series(
    device_id: uuid.UUID,
    user: CurrentUser,
    session: TenantSession,
    start: Annotated[
        datetime | None,
        Query(description="Window start (ISO-8601). Defaults to end - range_seconds."),
    ] = None,
    end: Annotated[datetime | None, Query(description="Window end. Defaults to now.")] = None,
    range_seconds: Annotated[
        int,
        Query(ge=60, le=MAX_RANGE_SECONDS, description="Used only when `start` is omitted."),
    ] = DEFAULT_RANGE_SECONDS,
    domains: Annotated[list[DomainName], Query()] = DEFAULT_DOMAINS,
    max_points: Annotated[
        int, Query(ge=MIN_MAX_POINTS, le=MAX_MAX_POINTS)
    ] = DEFAULT_MAX_POINTS,
) -> SeriesOut:
    exists = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Device)
        .where(Device.id == device_id, Device.deleted_at.is_(None))
    )
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    now = datetime.now(UTC)
    window_end = end or now
    window_start = start or window_end - timedelta(seconds=range_seconds)
    # A naive datetime on the wire would be compared against a tz-aware `now`
    # and raise; treat it as UTC, which is what the whole system speaks.
    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=UTC)
    if window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=UTC)

    if window_end <= window_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end must be after start",
        )
    try:
        plan = plan_series(window_start, window_end, max_points=max_points, now=now)
    except WindowTooWide as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    out = SeriesOut(
        device_id=device_id,
        start=window_start,
        end=window_end,
        source=plan.source.name,
        resolution_seconds=plan.bucket_seconds,
    )

    # dict.fromkeys rather than set(): duplicates are dropped but the caller's
    # order is kept, which keeps the response deterministic for a given request.
    for name in dict.fromkeys(domains):
        domain = DOMAINS_BY_SOURCE[DOMAIN_SOURCES[name]]
        rows, truncated = await fetch_series(
            session, device_id=device_id, domain=domain, plan=plan
        )
        model = POINT_MODELS[name]
        setattr(out, name, [model.model_validate(row) for row in rows])
        out.truncated = out.truncated or truncated

    return out
