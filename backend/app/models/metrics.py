"""Time-series metric tables.

Shape: one wide table per measurement domain, keyed by the entity that domain
is actually measured against — a mount point, a NIC, a latency target, a pid.
Typed columns mean Pydantic and SQL agree, storage stays compact, and Phase 4's
continuous aggregates are straightforward.

Every column is nullable unless the value is available on every platform. A
metric that cannot be read on a given OS is stored as NULL and rendered as
"unavailable" — it is never synthesised. Windows has no load average; macOS
exposes no temperature without an elevated helper; per-mount IO counters do not
exist at all (see the two disk tables below).

`resolution_seconds` records how the row was produced: 1 for a raw live sample,
10 for the agent's default aggregate window. It is not derivable after the fact
and analysis needs it, so it is stored rather than inferred.

Tenancy: user_id is denormalized onto every table so the RLS predicate is a
single indexed comparison instead of a join through devices. The composite FK
to devices (id, user_id) is what keeps that denormalization honest.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Chunk sizing: at 1s live resolution a single device writes ~86k rows/day into
# metric_samples. One-day chunks keep the working set in memory and make the
# 7-day retention policy a cheap chunk drop rather than a DELETE.
CHUNK_INTERVAL = "1 day"

RAW_RESOLUTION_SECONDS = 1
DEFAULT_RESOLUTION_SECONDS = 10


class _SampleBase:
    """Columns shared by every metric table."""

    device_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    # Part of every primary key: TimescaleDB requires the partitioning column
    # to appear in any unique index on a hypertable.
    ts: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), primary_key=True)
    resolution_seconds: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["device_id", "user_id"],
        ["devices.id", "devices.user_id"],
        name=f"fk_{table}_device_id_user_id_devices",
        ondelete="CASCADE",
    )


class MetricSample(_SampleBase, Base):
    """System-wide gauges. One row per device per timestamp."""

    __tablename__ = "metric_samples"
    __table_args__ = (
        _tenant_fk("metric_samples"),
        sa.Index("ix_metric_samples_user_id_ts", "user_id", "ts"),
    )

    # --- CPU ---
    cpu_percent: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    # min/max alongside the average only for the two metrics where a spike
    # inside the aggregation window carries real signal. p95 is deliberately
    # absent: over a 10-sample window it is indistinguishable from the max, and
    # it becomes meaningful in Phase 4's 1m/5m/1h rollups instead.
    cpu_percent_min: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    cpu_percent_max: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    cpu_user_percent: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    cpu_system_percent: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    cpu_iowait_percent: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    cpu_freq_mhz: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    ctx_switches_per_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    # Unix only; NULL on Windows.
    load1: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    load5: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    load15: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    # --- Memory ---
    mem_total_bytes: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    mem_used_bytes: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    mem_available_bytes: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    mem_percent: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    mem_percent_min: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    mem_percent_max: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    swap_total_bytes: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    swap_used_bytes: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    swap_percent: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    # --- System ---
    uptime_seconds: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    logged_in_users: Mapped[int | None] = mapped_column(sa.SmallInteger, nullable=True)
    process_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    active_connections: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    # Frequently unavailable: macOS needs an elevated helper, many VMs expose
    # neither, and desktops have no battery.
    temperature_celsius: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    fan_rpm: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    battery_percent: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    battery_plugged: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True)


class DiskUsageSample(_SampleBase, Base):
    """Capacity per mounted filesystem.

    Separate from DiskIoSample because the two are measured against different
    things: psutil reports usage per mount point and IO counters per physical
    disk, and mapping one onto the other is not reliably possible. Inventing
    that mapping would mean showing a number that was never measured.
    """

    __tablename__ = "disk_usage_samples"
    __table_args__ = (
        _tenant_fk("disk_usage_samples"),
        sa.Index("ix_disk_usage_samples_user_id_ts", "user_id", "ts"),
    )

    mount: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    filesystem: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    total_bytes: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    used_bytes: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    free_bytes: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    percent: Mapped[float | None] = mapped_column(sa.Float, nullable=True)


class DiskIoSample(_SampleBase, Base):
    """Throughput and IOPS per physical disk."""

    __tablename__ = "disk_io_samples"
    __table_args__ = (
        _tenant_fk("disk_io_samples"),
        sa.Index("ix_disk_io_samples_user_id_ts", "user_id", "ts"),
    )

    disk: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    # Rates, not the monotonic counters psutil returns: the agent differentiates
    # locally so a restart or a counter wrap cannot produce a false spike here.
    read_bytes_per_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    write_bytes_per_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    read_iops: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    write_iops: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    busy_percent: Mapped[float | None] = mapped_column(sa.Float, nullable=True)


class NetSample(_SampleBase, Base):
    """Throughput and errors per network interface."""

    __tablename__ = "net_samples"
    __table_args__ = (
        _tenant_fk("net_samples"),
        sa.Index("ix_net_samples_user_id_ts", "user_id", "ts"),
    )

    nic: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    tx_bytes_per_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    rx_bytes_per_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    tx_packets_per_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    rx_packets_per_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    errors_in_per_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    errors_out_per_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    drops_in_per_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    drops_out_per_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)


class LatencySample(_SampleBase, Base):
    """Round-trip time to a configured target."""

    __tablename__ = "latency_samples"
    __table_args__ = (
        _tenant_fk("latency_samples"),
        sa.Index("ix_latency_samples_user_id_ts", "user_id", "ts"),
    )

    target: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    # NULL rather than 0 when nothing came back — zero would read as a perfect
    # connection on every chart.
    rtt_ms_avg: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    rtt_ms_min: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    rtt_ms_max: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    packet_loss_percent: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    reachable: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)


class ProcessSample(_SampleBase, Base):
    """Top processes by CPU and by memory."""

    __tablename__ = "process_samples"
    __table_args__ = (
        _tenant_fk("process_samples"),
        sa.Index("ix_process_samples_user_id_ts", "user_id", "ts"),
        sa.CheckConstraint("rank_by IN ('cpu', 'memory')", name="rank_by"),
    )

    rank_by: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    rank: Mapped[int] = mapped_column(sa.SmallInteger, primary_key=True)
    pid: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    username: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    cpu_percent: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    memory_bytes: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)


#: (table, retention). Raw samples are kept for 7 days; Phase 4 adds the
#: continuous aggregates that carry longer horizons.
HYPERTABLES: tuple[str, ...] = (
    "metric_samples",
    "disk_usage_samples",
    "disk_io_samples",
    "net_samples",
    "latency_samples",
    "process_samples",
)
RAW_RETENTION = "7 days"
