"""Forecast state: the current Holt-Winters 24h forecast and time-to-exhaustion
projection per (device, metric), maintained by app/workers/forecast_worker.py.

Both tables follow AnomalyBaseline's shape exactly: one row per (device,
metric), created lazily on first computation, holding only the *current*
computed state rather than history. The worker overwrites a row every tick
regardless of whether anything changed — a forecast for a device that has
gone quiet should visibly age (via `computed_at`) rather than vanish.

`MetricForecast` covers the same 6-metric METRICS set alert rules use (see
models/alerts.py), continuing Phase 6's explicit "start narrow, widen later"
scoping decision. `ExhaustionForecast` covers the two metrics that actually
have a ceiling worth projecting toward: mem_percent and disk_percent.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.alerts import METRICS
from app.models.base import Base, in_check, uuid_pk

EXHAUSTION_METRICS = ("mem_percent", "disk_percent")


def _device_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["device_id", "user_id"],
        ["devices.id", "devices.user_id"],
        name=f"fk_{table}_device_id_user_id_devices",
        ondelete="CASCADE",
    )


class MetricForecast(Base):
    """The live 24h Holt-Winters forecast for one (device, metric).

    `points` is a JSONB list of `{offset_seconds, predicted, lower, upper}`
    dicts — analysis/forecast.py's `ForecastPoint` tuple, serialized. Empty
    when the worker last found too little history to fit one; the row still
    exists so `computed_at` can say when that was last checked.
    """

    __tablename__ = "metric_forecasts"
    __table_args__ = (
        sa.CheckConstraint(in_check("metric", METRICS), name="metric"),
        _device_fk("metric_forecasts"),
        sa.UniqueConstraint("device_id", "metric", name="uq_metric_forecasts_device_id_metric"),
        sa.Index("ix_metric_forecasts_user_id", "user_id"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    metric: Mapped[str] = mapped_column(sa.Text, nullable=False)
    #: Which mount (disk_percent) or target (packet_loss_percent) this
    #: forecast was actually fit against — see
    #: services/metrics_read.py's worst_entity_per_device(). Null for a
    #: single-entity metric (cpu/mem/swap/iowait), where the question
    #: doesn't apply.
    entity: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    computed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    horizon_seconds: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    bucket_seconds: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    points: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )


class ExhaustionForecast(Base):
    """The live disk/memory time-to-exhaustion projection for one (device,
    metric). See analysis/forecast.py's `estimate_time_to_exhaustion()` for
    the arithmetic; this table is just its last computed result."""

    __tablename__ = "exhaustion_forecasts"
    __table_args__ = (
        sa.CheckConstraint(in_check("metric", EXHAUSTION_METRICS), name="metric"),
        _device_fk("exhaustion_forecasts"),
        sa.UniqueConstraint(
            "device_id", "metric", name="uq_exhaustion_forecasts_device_id_metric"
        ),
        sa.Index("ix_exhaustion_forecasts_user_id", "user_id"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    metric: Mapped[str] = mapped_column(sa.Text, nullable=False)
    #: Which mount this projection was fit against for disk_percent; null
    #: for mem_percent, which has no entity.
    entity: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    computed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    #: The last real observed value the projection is anchored to.
    current_value: Mapped[float] = mapped_column(sa.Float, nullable=False)
    slope_per_day: Mapped[float] = mapped_column(sa.Float, nullable=False)
    #: None means "not projected to reach the ceiling on any actionable
    #: horizon" — see analysis/forecast.py's ExhaustionEstimate.
    projected_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
