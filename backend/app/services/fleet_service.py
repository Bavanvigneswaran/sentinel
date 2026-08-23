"""Builds the dashboard's whole payload in a fixed number of queries.

Six statements cover the entire fleet, whatever its size: the devices, one
rollup read that serves both the sparklines and the health window, and four
"latest row per entity" reads for the headline numbers. Nothing here loops a
query over devices — a fleet page that costs O(devices) round trips gets slower
in exactly the situation it exists for.

Freshness is explicit rather than implied. The headline numbers come only from
samples inside FRESH_WINDOW_SECONDS; past that the summary reports no readings
at all rather than presenting a twenty-minute-old figure as current. The health
score is computed over a five-minute mean instead of the newest single sample,
because one 10s reading of CPU is noise, not a state.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.health import HealthInputs, HealthResult, compute_health, unknown_health
from app.models import Device
from app.models.rollups import SYSTEM, TIERS_BY_NAME
from app.schemas.devices import derive_device_status
from app.services.metrics_read import FRESH_WINDOW_SECONDS, latest_per_entity

#: How far back a sparkline reaches by default.
DEFAULT_WINDOW_SECONDS = 3600
MIN_WINDOW_SECONDS = 300
MAX_WINDOW_SECONDS = 24 * 3600

#: Health is scored on a mean over this window, not on the newest sample.
HEALTH_WINDOW_SECONDS = 300

#: The rollup tier the sparkline and the health window are read from.
SPARKLINE_TIER = "1m"
SPARKLINE_RESOLUTION_SECONDS = TIERS_BY_NAME[SPARKLINE_TIER].seconds


@dataclass
class DeviceSummary:
    device: Device
    status: str
    health: HealthResult
    latest: dict[str, Any] | None
    disks: list[dict[str, Any]]
    sparkline_ts: list[datetime]
    sparkline_cpu: list[float | None]
    sparkline_mem: list[float | None]
    sparkline_resolution: int


def _sum_at_latest_ts(
    rows: list[dict[str, Any]], columns: tuple[str, ...]
) -> dict[uuid.UUID, dict[str, float | None]]:
    """Total each column across the entities a device reported *at its newest
    timestamp*.

    Restricting to that one timestamp matters: a NIC that stopped reporting two
    minutes ago still has a latest row, and adding its last known throughput to
    the current total would report traffic on an interface that is gone.
    """
    newest: dict[uuid.UUID, datetime] = {}
    for row in rows:
        device_id = row["device_id"]
        if device_id not in newest or row["ts"] > newest[device_id]:
            newest[device_id] = row["ts"]

    totals: dict[uuid.UUID, dict[str, float | None]] = defaultdict(
        lambda: dict.fromkeys(columns)
    )
    for row in rows:
        if row["ts"] != newest[row["device_id"]]:
            continue
        bucket = totals[row["device_id"]]
        for column in columns:
            value = row[column]
            if value is None:
                # NULL stays NULL until something real is added to it: a total
                # of "unavailable" is unavailable, not zero.
                continue
            bucket[column] = (bucket[column] or 0.0) + value
    return dict(totals)


def _weighted_mean(rows: list[dict[str, Any]], column: str) -> float | None:
    """Time-weighted mean over rollup rows, NULL-preserving.

    The same arithmetic the rollups themselves use — weight by measured seconds,
    ignore buckets where the metric was not measured — so a health score
    computed here matches a chart drawn from the same window.
    """
    total = 0.0
    weight = 0.0
    for row in rows:
        value = row[column]
        if value is None:
            continue
        w = float(row["sample_weight"] or 0)
        total += value * w
        weight += w
    return total / weight if weight else None


async def build_summaries(
    session: AsyncSession,
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    device_ids: list[uuid.UUID] | None = None,
    now: datetime | None = None,
) -> tuple[datetime, list[DeviceSummary]]:
    """Every summary the dashboard needs, read at one instant.

    The session must already be tenant-scoped: raw tables are covered by RLS and
    the rollup is reached through its scoped wrapper view, so neither this
    function nor its caller filters by user_id.
    """
    now = now or datetime.now(UTC)
    window_seconds = max(MIN_WINDOW_SECONDS, min(MAX_WINDOW_SECONDS, window_seconds))
    window_since = now - timedelta(seconds=window_seconds)
    fresh_since = now - timedelta(seconds=FRESH_WINDOW_SECONDS)
    health_since = now - timedelta(seconds=HEALTH_WINDOW_SECONDS)

    query = sa.select(Device).where(Device.deleted_at.is_(None))
    if device_ids:
        query = query.where(Device.id.in_(device_ids))
    devices = list(await session.scalars(query.order_by(Device.created_at)))
    if not devices:
        return now, []

    ids = [d.id for d in devices]

    # One rollup read serves both the sparkline and the health window. Reading
    # them separately would let the score disagree with the chart beside it.
    rollup_rows = await session.execute(
        sa.text(
            "SELECT device_id, ts, cpu_percent, mem_percent, swap_percent, "  # noqa: S608
            "cpu_iowait_percent, sample_weight "
            f"FROM {SYSTEM.view_name_for(SPARKLINE_TIER)} "
            "WHERE ts >= :since AND device_id = ANY(:device_ids) "
            "ORDER BY device_id, ts"
        ),
        {"since": window_since, "device_ids": ids},
    )
    by_device: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in rollup_rows.mappings():
        by_device[row["device_id"]].append(dict(row))

    latest_system = {
        row["device_id"]: row
        for row in await latest_per_entity(
            session,
            table="metric_samples",
            entity_keys=(),
            columns=(
                "cpu_percent",
                "mem_percent",
                "mem_used_bytes",
                "mem_total_bytes",
                "swap_percent",
                "load1",
                "uptime_seconds",
                "process_count",
                "battery_percent",
                "battery_plugged",
                "temperature_celsius",
            ),
            since=fresh_since,
            device_ids=ids,
        )
    }

    disk_rows = await latest_per_entity(
        session,
        table="disk_usage_samples",
        entity_keys=("mount",),
        columns=("filesystem", "total_bytes", "used_bytes", "free_bytes", "percent"),
        since=fresh_since,
        device_ids=ids,
    )
    disks_by_device: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in disk_rows:
        disks_by_device[row["device_id"]].append(row)

    net_totals = _sum_at_latest_ts(
        await latest_per_entity(
            session,
            table="net_samples",
            entity_keys=("nic",),
            columns=("rx_bytes_per_s", "tx_bytes_per_s"),
            since=fresh_since,
            device_ids=ids,
        ),
        ("rx_bytes_per_s", "tx_bytes_per_s"),
    )
    io_totals = _sum_at_latest_ts(
        await latest_per_entity(
            session,
            table="disk_io_samples",
            entity_keys=("disk",),
            columns=("read_bytes_per_s", "write_bytes_per_s"),
            since=fresh_since,
            device_ids=ids,
        ),
        ("read_bytes_per_s", "write_bytes_per_s"),
    )

    latency_rows = await latest_per_entity(
        session,
        table="latency_samples",
        entity_keys=("target",),
        columns=("rtt_ms_avg", "packet_loss_percent", "reachable"),
        since=fresh_since,
        device_ids=ids,
    )
    worst_latency: dict[uuid.UUID, dict[str, Any]] = {}
    for row in latency_rows:
        current = worst_latency.get(row["device_id"])
        # Worst target, not the mean: one dead link is the thing worth showing,
        # and averaging it against three healthy ones hides it.
        if current is None or (row["packet_loss_percent"] or 0) > (
            current["packet_loss_percent"] or 0
        ):
            worst_latency[row["device_id"]] = row

    summaries = []
    for device in devices:
        status = derive_device_status(device.status, device.last_seen_at)
        rows = by_device.get(device.id, [])
        disks = sorted(
            disks_by_device.get(device.id, []),
            key=lambda d: (d["percent"] is None, -(d["percent"] or 0)),
        )
        system = latest_system.get(device.id)
        latency = worst_latency.get(device.id)

        if status != "online":
            # Its last reading describes a moment that has passed. Scoring it
            # would be a claim about a machine nobody is talking to.
            health = unknown_health("device is offline")
        else:
            health_rows = [r for r in rows if r["ts"] >= health_since]
            health = compute_health(
                HealthInputs(
                    cpu_percent=_weighted_mean(health_rows, "cpu_percent"),
                    memory_percent=_weighted_mean(health_rows, "mem_percent"),
                    swap_percent=_weighted_mean(health_rows, "swap_percent"),
                    cpu_iowait_percent=_weighted_mean(health_rows, "cpu_iowait_percent"),
                    # Capacity is a level, not a rate: the newest reading is the
                    # truth, and averaging it would lag a disk filling up.
                    disk_percent=next(
                        (d["percent"] for d in disks if d["percent"] is not None), None
                    ),
                    packet_loss_percent=(
                        latency["packet_loss_percent"] if latency else None
                    ),
                )
            )

        latest: dict[str, Any] | None = None
        if system is not None:
            latest = dict(system)
            latest.pop("device_id", None)
            net = net_totals.get(device.id, {})
            io = io_totals.get(device.id, {})
            latest["net_rx_bytes_per_s"] = net.get("rx_bytes_per_s")
            latest["net_tx_bytes_per_s"] = net.get("tx_bytes_per_s")
            latest["disk_read_bytes_per_s"] = io.get("read_bytes_per_s")
            latest["disk_write_bytes_per_s"] = io.get("write_bytes_per_s")
            latest["packet_loss_percent"] = (
                latency["packet_loss_percent"] if latency else None
            )
            latest["rtt_ms_avg"] = latency["rtt_ms_avg"] if latency else None

        summaries.append(
            DeviceSummary(
                device=device,
                status=status,
                health=health,
                latest=latest,
                disks=disks,
                sparkline_ts=[r["ts"] for r in rows],
                sparkline_cpu=[r["cpu_percent"] for r in rows],
                sparkline_mem=[r["mem_percent"] for r in rows],
                sparkline_resolution=SPARKLINE_RESOLUTION_SECONDS,
            )
        )

    return now, summaries
