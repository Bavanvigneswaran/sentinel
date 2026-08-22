"""Wire schemas for the live forecast/exhaustion state app/workers/forecast_worker.py
maintains. Mirrors app/schemas/alerts.py's AnomalyBaselineOut in spirit: these
expose only *current* computed state, not history.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

Metric = Literal[
    "cpu_percent",
    "mem_percent",
    "swap_percent",
    "disk_percent",
    "packet_loss_percent",
    "cpu_iowait_percent",
]
ExhaustionMetric = Literal["mem_percent", "disk_percent"]


class ForecastPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    offset_seconds: int
    predicted: float
    lower: float
    upper: float


class MetricForecastOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: uuid.UUID
    metric: Metric
    #: Which mount/target this was actually fit against — null for a
    #: single-entity metric. See app/models/forecasts.py.
    entity: str | None
    computed_at: datetime
    horizon_seconds: int
    bucket_seconds: int
    #: Empty when the worker last found too little history to fit one —
    #: still returned (rather than omitted) so the caller can see when that
    #: was last checked via computed_at.
    points: list[ForecastPointOut]


class ExhaustionForecastOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: uuid.UUID
    metric: ExhaustionMetric
    #: Which mount this was fit against for disk_percent; null for mem_percent.
    entity: str | None
    computed_at: datetime
    current_value: float
    slope_per_day: float
    #: None means not projected to reach capacity on any actionable horizon
    #: — see analysis/forecast.py's ExhaustionEstimate.
    projected_at: datetime | None
