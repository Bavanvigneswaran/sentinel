"""Assembles the structured payload app/insights/generator.py turns into
language for one incident: the device it happened on, the AlertEvents
correlated into it (with whatever evidence their rule_type carries), the
device's current health score, and the forecast/anomaly-baseline state for
whichever metrics are actually involved.

Every field here is either a real measured/computed value or explicitly
None/empty — nothing is invented to fill a gap, the same posture
analysis/health.py and analysis/forecast.py already take. This is *data*;
app/insights/generator.py is the one place that decides how it is presented,
and every None here becomes a dropped clause there rather than a printed
"unknown".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.health import HealthResult, unknown_health
from app.models import AlertEvent, AnomalyBaseline, Device, ExhaustionForecast, MetricForecast
from app.models.incidents import Incident
from app.services.fleet_service import build_summaries


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


@dataclass(frozen=True)
class EventSnapshot:
    rule_name: str
    rule_type: str
    metric: str
    comparison: str | None
    threshold: float | None
    status: str
    value_at_fire: float
    last_value: float | None
    fired_at: datetime
    resolved_at: datetime | None
    resolved_value: float | None
    observed_value: float | None
    baseline_mean: float | None
    baseline_mad: float | None
    z_score: float | None
    predicted_value: float | None
    predicted_breach_at: datetime | None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "rule_type": self.rule_type,
            "metric": self.metric,
            "comparison": self.comparison,
            "threshold": self.threshold,
            "status": self.status,
            "value_at_fire": self.value_at_fire,
            "last_value": self.last_value,
            "fired_at": _iso(self.fired_at),
            "resolved_at": _iso(self.resolved_at),
            "resolved_value": self.resolved_value,
            "observed_value": self.observed_value,
            "baseline_mean": self.baseline_mean,
            "baseline_mad": self.baseline_mad,
            "z_score": self.z_score,
            "predicted_value": self.predicted_value,
            "predicted_breach_at": _iso(self.predicted_breach_at),
        }


@dataclass(frozen=True)
class ForecastSnapshot:
    metric: str
    entity: str | None
    computed_at: datetime
    horizon_seconds: int
    #: The nearest and furthest predicted points, not the full series — an
    #: LLM prompt needs the trend's direction and magnitude, not every bucket.
    first_predicted: float | None
    last_predicted: float | None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "entity": self.entity,
            "computed_at": _iso(self.computed_at),
            "horizon_seconds": self.horizon_seconds,
            "first_predicted": self.first_predicted,
            "last_predicted": self.last_predicted,
        }


@dataclass(frozen=True)
class ExhaustionSnapshot:
    metric: str
    entity: str | None
    current_value: float
    slope_per_day: float
    projected_at: datetime | None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "entity": self.entity,
            "current_value": self.current_value,
            "slope_per_day": self.slope_per_day,
            "projected_at": _iso(self.projected_at),
        }


@dataclass(frozen=True)
class AnomalyBaselineSnapshot:
    metric: str
    mean: float
    mad: float
    sample_count: int

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "mean": self.mean,
            "mad": self.mad,
            "sample_count": self.sample_count,
        }


@dataclass(frozen=True)
class DeviceSnapshot:
    name: str
    hostname: str | None
    os: str | None
    os_version: str | None
    platform: str
    cpu_cores: int | None
    total_memory_bytes: int | None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "hostname": self.hostname,
            "os": self.os,
            "os_version": self.os_version,
            "platform": self.platform,
            "cpu_cores": self.cpu_cores,
            "total_memory_bytes": self.total_memory_bytes,
        }


@dataclass(frozen=True)
class HealthSnapshot:
    score: int | None
    band: str
    reason: str | None
    #: (key, label, value, unit, score, weight) for each component the
    #: health computation defines — value/score are None for one the
    #: platform did not report, exactly as analysis/health.py leaves them.
    components: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "band": self.band,
            "reason": self.reason,
            "components": list(self.components),
        }

    @staticmethod
    def from_health_result(result: HealthResult) -> HealthSnapshot:
        return HealthSnapshot(
            score=result.score,
            band=result.band,
            reason=result.reason,
            components=tuple(
                {
                    "key": c.key,
                    "label": c.label,
                    "value": c.value,
                    "unit": c.unit,
                    "score": c.score,
                    "weight": c.weight,
                }
                for c in result.components
            ),
        )


@dataclass(frozen=True)
class SignalBundle:
    incident_status: str
    opened_at: datetime
    closed_at: datetime | None
    device: DeviceSnapshot
    events: tuple[EventSnapshot, ...]
    health: HealthSnapshot
    forecasts: tuple[ForecastSnapshot, ...]
    exhaustion: tuple[ExhaustionSnapshot, ...]
    anomaly_baselines: tuple[AnomalyBaselineSnapshot, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "incident_status": self.incident_status,
            "opened_at": _iso(self.opened_at),
            "closed_at": _iso(self.closed_at),
            "device": self.device.to_json_dict(),
            "events": [e.to_json_dict() for e in self.events],
            "health": self.health.to_json_dict(),
            "forecasts": [f.to_json_dict() for f in self.forecasts],
            "exhaustion": [e.to_json_dict() for e in self.exhaustion],
            "anomaly_baselines": [b.to_json_dict() for b in self.anomaly_baselines],
        }


def _event_snapshot(event: AlertEvent) -> EventSnapshot:
    return EventSnapshot(
        rule_name=event.rule_name,
        rule_type=event.rule_type,
        metric=event.metric,
        comparison=event.comparison,
        threshold=event.threshold,
        status=event.status,
        value_at_fire=event.value_at_fire,
        last_value=event.last_value,
        fired_at=event.fired_at,
        resolved_at=event.resolved_at,
        resolved_value=event.resolved_value,
        observed_value=event.observed_value,
        baseline_mean=event.baseline_mean,
        baseline_mad=event.baseline_mad,
        z_score=event.z_score,
        predicted_value=event.predicted_value,
        predicted_breach_at=event.predicted_breach_at,
    )


def _forecast_snapshot(row: MetricForecast) -> ForecastSnapshot:
    points = row.points or []
    return ForecastSnapshot(
        metric=row.metric,
        entity=row.entity,
        computed_at=row.computed_at,
        horizon_seconds=row.horizon_seconds,
        first_predicted=points[0]["predicted"] if points else None,
        last_predicted=points[-1]["predicted"] if points else None,
    )


async def build_signal_bundle(session: AsyncSession, incident: Incident) -> SignalBundle:
    """The session must already be tenant-scoped, same requirement as
    fleet_service.build_summaries()."""
    device = await session.get(Device, incident.device_id)
    assert device is not None  # noqa: S101 — the composite FK guarantees this

    events = list(
        await session.scalars(
            sa.select(AlertEvent)
            .where(AlertEvent.incident_id == incident.id)
            .order_by(AlertEvent.fired_at)
        )
    )
    metrics_involved = sorted({e.metric for e in events})

    _now, summaries = await build_summaries(session, device_ids=[incident.device_id])
    if summaries:
        health = summaries[0].health
    else:
        # build_summaries() filters soft-deleted devices, so the overwhelmingly
        # common way to land here is an incident whose device was removed —
        # session.get() above still found it, because a soft delete keeps the
        # row. Saying "device not found" for that is the same conflation
        # lib/deviceNames.ts exists to prevent on both frontends: "removed" is
        # permanent and explainable, "missing" is a fault. Only the genuinely
        # unexplained case keeps the vaguer wording.
        reason = "device has been removed" if device.deleted_at else "no current readings"
        health = unknown_health(reason)

    forecasts: list[MetricForecast] = []
    exhaustion: list[ExhaustionForecast] = []
    baselines: list[AnomalyBaseline] = []
    if metrics_involved:
        forecasts = list(
            await session.scalars(
                sa.select(MetricForecast).where(
                    MetricForecast.device_id == incident.device_id,
                    MetricForecast.metric.in_(metrics_involved),
                )
            )
        )
        exhaustion = list(
            await session.scalars(
                sa.select(ExhaustionForecast).where(
                    ExhaustionForecast.device_id == incident.device_id,
                    ExhaustionForecast.metric.in_(metrics_involved),
                )
            )
        )
        baselines = list(
            await session.scalars(
                sa.select(AnomalyBaseline).where(
                    AnomalyBaseline.device_id == incident.device_id,
                    AnomalyBaseline.metric.in_(metrics_involved),
                )
            )
        )

    return SignalBundle(
        incident_status=incident.status,
        opened_at=incident.opened_at,
        closed_at=incident.closed_at,
        device=DeviceSnapshot(
            name=device.name,
            hostname=device.hostname,
            os=device.os,
            os_version=device.os_version,
            platform=device.platform,
            cpu_cores=device.cpu_cores,
            total_memory_bytes=device.total_memory_bytes,
        ),
        events=tuple(_event_snapshot(e) for e in events),
        health=HealthSnapshot.from_health_result(health),
        forecasts=tuple(_forecast_snapshot(f) for f in forecasts),
        exhaustion=tuple(
            ExhaustionSnapshot(
                metric=e.metric,
                entity=e.entity,
                current_value=e.current_value,
                slope_per_day=e.slope_per_day,
                projected_at=e.projected_at,
            )
            for e in exhaustion
        ),
        anomaly_baselines=tuple(
            AnomalyBaselineSnapshot(
                metric=b.metric, mean=b.mean, mad=b.mad, sample_count=b.sample_count
            )
            for b in baselines
        ),
    )


async def fetch_event_membership(session: AsyncSession, incident_id: uuid.UUID) -> list:
    """The cheap half of what build_signal_bundle reads: just enough per
    event (id, status, resolved_at) to compute
    analysis/incidents.py's correlation_fingerprint(). Kept separate so
    app/insights/service.py can decide "has anything changed" without
    paying for the full bundle (health score, forecasts, baselines) when the
    answer is no.
    """
    from app.analysis.incidents import EventMembership

    rows = await session.execute(
        sa.select(AlertEvent.id, AlertEvent.status, AlertEvent.resolved_at).where(
            AlertEvent.incident_id == incident_id
        )
    )
    return [EventMembership(*row) for row in rows]
