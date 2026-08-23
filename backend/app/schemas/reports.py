"""Wire schemas for the analytics/reports API: the JSON view of
services/report_service.py's ReportBundle, and scheduled-report CRUD.

The analytics schemas are plain BaseModels (not `from_attributes`) —
api/routes/reports.py builds them field-by-field from the ReportBundle
dataclass tree rather than relying on attribute-name matching, since
`DeviceAnalytics.device` is a whole Device ORM object where the wire shape
wants a flat `device_id`/`device_name`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Metric = Literal[
    "cpu_percent",
    "mem_percent",
    "swap_percent",
    "disk_percent",
    "packet_loss_percent",
    "cpu_iowait_percent",
]
ReportCadence = Literal["weekly", "monthly"]
ReportFormat = Literal["pdf", "csv"]


# --- analytics -----------------------------------------------------------------


class PeriodStatsOut(BaseModel):
    avg: float | None
    min: float | None
    max: float | None


class MetricTrendOut(BaseModel):
    metric: Metric
    #: The mount (disk_percent) or target (packet_loss_percent) this trend
    #: was computed against — None for a single-entity metric.
    entity: str | None
    current: PeriodStatsOut
    previous: PeriodStatsOut
    delta_percent: float | None


class AvailabilityOut(BaseModel):
    uptime_percent: float | None
    reporting_seconds: float
    period_seconds: float


class ReliabilityOut(BaseModel):
    incident_count: int
    resolved_incident_count: int
    mean_time_to_resolve_seconds: float | None
    alert_fired_count: int


class DeviceAnalyticsOut(BaseModel):
    device_id: uuid.UUID
    device_name: str
    availability: AvailabilityOut
    reliability: ReliabilityOut
    trends: list[MetricTrendOut]


class AnalyticsReportOut(BaseModel):
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    period_days: int
    devices: list[DeviceAnalyticsOut]


# --- scheduled reports -----------------------------------------------------


class ReportScheduleCreate(BaseModel):
    #: None reports on the whole fleet.
    device_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    period_days: int = Field(default=30, ge=1, le=366)
    cadence: ReportCadence
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    format: ReportFormat = "pdf"
    #: Empty means "the account's own email".
    recipients: list[str] = Field(default_factory=list)
    enabled: bool = True

    @model_validator(mode="after")
    def _day_field_matches_cadence(self) -> ReportScheduleCreate:
        if self.cadence == "weekly":
            if self.day_of_week is None or self.day_of_month is not None:
                raise ValueError(
                    "weekly schedules require day_of_week and must not set day_of_month"
                )
        elif self.day_of_month is None or self.day_of_week is not None:
            raise ValueError("monthly schedules require day_of_month and must not set day_of_week")
        return self


class ReportScheduleUpdate(BaseModel):
    device_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    period_days: int | None = Field(default=None, ge=1, le=366)
    cadence: ReportCadence | None = None
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    format: ReportFormat | None = None
    recipients: list[str] | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _day_field_matches_cadence_change(self) -> ReportScheduleUpdate:
        # Only checked when a request actually flips cadence — same posture
        # as AlertRuleUpdate's own validator: it cannot see the row's
        # existing cadence, only what this PATCH submitted, so it validates
        # what it can and leaves the rest to the DB CHECK.
        weekly_missing_day = self.day_of_week is None or self.day_of_month is not None
        if self.cadence == "weekly" and weekly_missing_day:
            raise ValueError("switching to weekly requires day_of_week in the same request")
        monthly_missing_day = self.day_of_month is None or self.day_of_week is not None
        if self.cadence == "monthly" and monthly_missing_day:
            raise ValueError("switching to monthly requires day_of_month in the same request")
        return self


class ReportScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID | None
    name: str
    period_days: int
    cadence: ReportCadence
    day_of_week: int | None
    day_of_month: int | None
    format: ReportFormat
    recipients: list[str]
    enabled: bool
    last_sent_at: datetime | None
    created_at: datetime
    updated_at: datetime
