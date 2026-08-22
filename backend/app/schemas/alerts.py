"""Wire schemas for alert rules, firing history, and silences."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.analysis.anomaly import Severity, classify_severity

Metric = Literal[
    "cpu_percent",
    "mem_percent",
    "swap_percent",
    "disk_percent",
    "packet_loss_percent",
    "cpu_iowait_percent",
]
Comparison = Literal[">", ">=", "<", "<=", "=="]
RuleType = Literal["threshold", "anomaly", "forecast"]
AlertState = Literal["ok", "pending", "firing"]
EventStatus = Literal["firing", "resolved"]


class AlertRuleCreate(BaseModel):
    #: None applies the rule to every one of the caller's devices.
    device_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    rule_type: RuleType = "threshold"
    metric: Metric
    #: Required for a threshold or forecast rule, must be omitted for an
    #: anomaly rule — enforced below, mirroring the DB's rule_type_fields
    #: CHECK constraint at the API boundary so a bad combination fails with
    #: a 422, not a raw integrity error.
    comparison: Comparison | None = None
    threshold: float | None = None
    for_duration_seconds: int = Field(default=60, ge=0, le=86400)
    enabled: bool = True

    @model_validator(mode="after")
    def _fields_match_rule_type(self) -> AlertRuleCreate:
        if self.rule_type in ("threshold", "forecast"):
            if self.comparison is None or self.threshold is None:
                raise ValueError(f"{self.rule_type} rules require comparison and threshold")
        elif self.comparison is not None or self.threshold is not None:
            raise ValueError("anomaly rules must not set comparison or threshold")
        return self


class AlertRuleUpdate(BaseModel):
    """All fields optional; PATCH applies only what's present. Because a
    PATCH may touch only some fields, this validator can only check the
    combination actually submitted — it cannot see the rule's existing
    rule_type. The route rejects a rule_type flip that isn't accompanied by
    consistent comparison/threshold in the same request; the DB CHECK is the
    final backstop either way.
    """

    device_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    rule_type: RuleType | None = None
    metric: Metric | None = None
    comparison: Comparison | None = None
    threshold: float | None = None
    for_duration_seconds: int | None = Field(default=None, ge=0, le=86400)
    enabled: bool | None = None

    @model_validator(mode="after")
    def _rule_type_change_carries_matching_fields(self) -> AlertRuleUpdate:
        if self.rule_type in ("threshold", "forecast") and (
            self.comparison is None or self.threshold is None
        ):
            raise ValueError(
                f"switching a rule to rule_type={self.rule_type} requires comparison and "
                "threshold in the same request"
            )
        if self.rule_type == "anomaly" and (
            self.comparison is not None or self.threshold is not None
        ):
            raise ValueError(
                "switching a rule to rule_type=anomaly must not set comparison or threshold"
            )
        return self


class AlertRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID | None
    name: str
    rule_type: RuleType
    metric: Metric
    comparison: Comparison | None
    threshold: float | None
    for_duration_seconds: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AlertEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_id: uuid.UUID | None
    device_id: uuid.UUID
    #: The incident this event was correlated into — see
    #: app/alerts/incident_apply.py. Set synchronously at fire time, so
    #: always populated for any event this schema ever serializes.
    incident_id: uuid.UUID | None
    #: Snapshotted from the rule at fire time — correct even after the rule
    #: is later edited or deleted. `rule_type` is the reliable discriminator
    #: for which evidence fields below are populated (comparison/threshold
    #: are set for both threshold and forecast events, so comparison alone
    #: no longer distinguishes every kind).
    rule_name: str
    rule_type: RuleType
    metric: Metric
    comparison: Comparison | None
    threshold: float | None
    status: EventStatus
    value_at_fire: float
    last_value: float | None
    fired_at: datetime
    resolved_at: datetime | None
    resolved_value: float | None
    notified_at: datetime | None
    observed_value: float | None
    baseline_mean: float | None
    baseline_mad: float | None
    z_score: float | None
    #: Populated only for a forecast-sourced event.
    predicted_breach_at: datetime | None
    predicted_value: float | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def severity(self) -> Severity | None:
        #: Derived from z_score at read time, never stored — a second,
        #: potentially-stale copy of information z_score already carries is
        #: exactly what CLAUDE.md's "never synthesise" rule rules out.
        if self.z_score is None:
            return None
        return classify_severity(self.z_score)


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


class AnomalyBaselineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: uuid.UUID
    metric: Metric
    mean: float
    #: Unscaled EWMA absolute deviation — see analysis/anomaly.py.
    mad: float
    sample_count: int
    updated_at: datetime
