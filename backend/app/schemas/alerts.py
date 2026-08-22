"""Wire schemas for alert rules, firing history, and silences."""

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
Comparison = Literal[">", ">=", "<", "<=", "=="]
AlertState = Literal["ok", "pending", "firing"]
EventStatus = Literal["firing", "resolved"]


class AlertRuleCreate(BaseModel):
    #: None applies the rule to every one of the caller's devices.
    device_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    metric: Metric
    comparison: Comparison
    threshold: float
    for_duration_seconds: int = Field(default=60, ge=0, le=86400)
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    """All fields optional; PATCH applies only what's present."""

    device_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    metric: Metric | None = None
    comparison: Comparison | None = None
    threshold: float | None = None
    for_duration_seconds: int | None = Field(default=None, ge=0, le=86400)
    enabled: bool | None = None


class AlertRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID | None
    name: str
    metric: Metric
    comparison: Comparison
    threshold: float
    for_duration_seconds: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AlertEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_id: uuid.UUID | None
    device_id: uuid.UUID
    #: Snapshotted from the rule at fire time — correct even after the rule
    #: is later edited or deleted.
    rule_name: str
    metric: Metric
    comparison: Comparison
    threshold: float
    status: EventStatus
    value_at_fire: float
    last_value: float | None
    fired_at: datetime
    resolved_at: datetime | None
    resolved_value: float | None
    notified_at: datetime | None


class AlertSilenceCreate(BaseModel):
    #: None silences every device / every rule respectively.
    device_id: uuid.UUID | None = None
    rule_id: uuid.UUID | None = None
    starts_at: datetime
    ends_at: datetime
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _ends_after_starts(self) -> AlertSilenceCreate:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class AlertSilenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID | None
    rule_id: uuid.UUID | None
    starts_at: datetime
    ends_at: datetime
    reason: str | None
    created_at: datetime
