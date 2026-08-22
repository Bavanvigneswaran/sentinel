"""The dashboard's endpoints: fleet overview and per-device summary.

Both are the same computation over a different device set, so they share one
service call — a per-device summary is a fleet of one, and building it any other
way would let the two disagree about what "healthy" means.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.analysis.health import HealthResult
from app.api.deps import CurrentUser, TenantSession
from app.schemas.devices import DeviceOut
from app.schemas.fleet import (
    DeviceSummaryOut,
    FleetOverviewOut,
    FleetTotalsOut,
    HealthComponentOut,
    HealthOut,
    LatestReadingsOut,
    MountUsageOut,
    SparklineOut,
)
from app.services import fleet_service as svc
from app.services.fleet_service import DeviceSummary

router = APIRouter(tags=["fleet"])


def _health_out(health: HealthResult) -> HealthOut:
    return HealthOut(
        score=health.score,
        band=health.band,
        components=[
            HealthComponentOut(
                key=c.key,
                label=c.label,
                value=c.value,
                unit=c.unit,
                score=c.score,
                weight=c.weight,
            )
            for c in health.components
        ],
        unavailable=list(health.unavailable),
        reason=health.reason,
    )


def _summary_out(summary: DeviceSummary) -> DeviceSummaryOut:
    device = DeviceOut.model_validate(summary.device)
    # DeviceOut derives staleness itself, and the service derived it too. They
    # agree by construction — both call derive_device_status — and this asserts
    # the one that the rest of the payload was actually built against.
    device.status = summary.status

    return DeviceSummaryOut(
        device=device,
        health=_health_out(summary.health),
        latest=(
            LatestReadingsOut(**summary.latest) if summary.latest is not None else None
        ),
        disks=[
            MountUsageOut(**{k: v for k, v in d.items() if k != "device_id"})
            for d in summary.disks
        ],
        sparkline=SparklineOut(
            resolution_seconds=summary.sparkline_resolution,
            ts=summary.sparkline_ts,
            cpu_percent=summary.sparkline_cpu,
            mem_percent=summary.sparkline_mem,
        ),
    )


def _totals(summaries: list[DeviceSummary]) -> FleetTotalsOut:
    counts = {"online": 0, "offline": 0, "pending": 0}
    bands = {"healthy": 0, "degraded": 0, "critical": 0, "unknown": 0}
    for summary in summaries:
        counts[summary.status] = counts.get(summary.status, 0) + 1
        bands[summary.health.band] += 1
    return FleetTotalsOut(devices=len(summaries), **counts, **bands)


@router.get("/fleet/overview", response_model=FleetOverviewOut)
async def fleet_overview(
    user: CurrentUser,
    session: TenantSession,
    window_seconds: int = Query(
        default=svc.DEFAULT_WINDOW_SECONDS,
        ge=svc.MIN_WINDOW_SECONDS,
        le=svc.MAX_WINDOW_SECONDS,
        description="How far back the sparklines reach.",
    ),
) -> FleetOverviewOut:
    generated_at, summaries = await svc.build_summaries(
        session, window_seconds=window_seconds
    )
    return FleetOverviewOut(
        generated_at=generated_at,
        window_seconds=window_seconds,
        totals=_totals(summaries),
        devices=[_summary_out(s) for s in summaries],
    )


@router.get("/devices/{device_id}", response_model=DeviceOut)
async def get_device(
    device_id: uuid.UUID, user: CurrentUser, session: TenantSession
) -> DeviceOut:
    """One device. RLS makes "not yours" and "not there" the same 404."""
    _, summaries = await svc.build_summaries(session, device_ids=[device_id])
    if not summaries:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return _summary_out(summaries[0]).device


@router.get("/devices/{device_id}/summary", response_model=DeviceSummaryOut)
async def device_summary(
    device_id: uuid.UUID,
    user: CurrentUser,
    session: TenantSession,
    window_seconds: int = Query(
        default=svc.DEFAULT_WINDOW_SECONDS,
        ge=svc.MIN_WINDOW_SECONDS,
        le=svc.MAX_WINDOW_SECONDS,
    ),
) -> DeviceSummaryOut:
    _, summaries = await svc.build_summaries(
        session, window_seconds=window_seconds, device_ids=[device_id]
    )
    if not summaries:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return _summary_out(summaries[0])
