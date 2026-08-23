"""Read-only access to the live forecast/exhaustion state
app/workers/forecast_worker.py maintains — same shape as alerts.py's
`list_anomaly_baselines`: no pagination (small-N per user), optional
device_id filter, current state only, not history.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter

from app.api.deps import CurrentUser, TenantSession
from app.models import Device, ExhaustionForecast, MetricForecast
from app.schemas.forecast import ExhaustionForecastOut, MetricForecastOut

router = APIRouter(tags=["forecasts"])


def _live_devices_only(query, model):  # noqa: ANN001, ANN201
    """Drop rows belonging to a soft-deleted device.

    A forecast is a claim about where a machine is *heading*, so keeping one
    for a machine the user has removed is stating a future for something that
    no longer exists. The rows themselves are left alone — the delete is soft
    precisely so the metric history behind them stays valid — they are just not
    read back.

    Visible the moment device removal became a real button rather than a
    curl command: the Forecasts page listed four removed phones as "full in
    22h", "full in 3.2 days", each identified by a bare UUID, because
    `/devices` correctly omits deleted devices and so the page had no name to
    render. `fleet_service.build_summaries()` and `report_service` have always
    filtered this way; these two endpoints never did.
    """
    return query.where(
        sa.exists().where(Device.id == model.device_id, Device.deleted_at.is_(None))
    )


@router.get("/forecasts", response_model=list[MetricForecastOut])
async def list_forecasts(
    user: CurrentUser, session: TenantSession, device_id: uuid.UUID | None = None
) -> list[MetricForecast]:
    query = _live_devices_only(sa.select(MetricForecast), MetricForecast)
    if device_id is not None:
        query = query.where(MetricForecast.device_id == device_id)
    rows = await session.scalars(query)
    return list(rows)


@router.get("/forecasts/exhaustion", response_model=list[ExhaustionForecastOut])
async def list_exhaustion_forecasts(
    user: CurrentUser, session: TenantSession, device_id: uuid.UUID | None = None
) -> list[ExhaustionForecast]:
    query = _live_devices_only(sa.select(ExhaustionForecast), ExhaustionForecast)
    if device_id is not None:
        query = query.where(ExhaustionForecast.device_id == device_id)
    rows = await session.scalars(query)
    return list(rows)
