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
from app.models import ExhaustionForecast, MetricForecast
from app.schemas.forecast import ExhaustionForecastOut, MetricForecastOut

router = APIRouter(tags=["forecasts"])


@router.get("/forecasts", response_model=list[MetricForecastOut])
async def list_forecasts(
    user: CurrentUser, session: TenantSession, device_id: uuid.UUID | None = None
) -> list[MetricForecast]:
    query = sa.select(MetricForecast)
    if device_id is not None:
        query = query.where(MetricForecast.device_id == device_id)
    rows = await session.scalars(query)
    return list(rows)


@router.get("/forecasts/exhaustion", response_model=list[ExhaustionForecastOut])
async def list_exhaustion_forecasts(
    user: CurrentUser, session: TenantSession, device_id: uuid.UUID | None = None
) -> list[ExhaustionForecast]:
    query = sa.select(ExhaustionForecast)
    if device_id is not None:
        query = query.where(ExhaustionForecast.device_id == device_id)
    rows = await session.scalars(query)
    return list(rows)
