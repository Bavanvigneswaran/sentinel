"""Wire schemas for the time-range query API.

Each point extends the corresponding agent protocol entry, because a rolled-up
point carries exactly the same columns as a raw one — that identity is enforced
by app/models/rollups.py, where every aggregate is named after the raw column it
summarises. What a rollup adds is the bookkeeping the raw row does not need:
how much measured time went into the bucket, and how many rows.

`sample_count` is not decoration. A bucket built from one sample and a bucket
built from six are both "a minute of CPU", and only this field distinguishes a
quiet machine from one that was disconnected for most of the minute.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.protocol import (
    DiskIoEntry,
    DiskUsageEntry,
    LatencyEntry,
    NetEntry,
    SystemSample,
)

SourceName = Literal["raw", "1m", "5m", "1h"]

DomainName = Literal["system", "disk_usage", "disk_io", "net", "latency"]

#: Maps the request's domain name onto the rollup spec's source table. The API
#: never accepts a table name directly — a name outside this mapping is a 422
#: before anything reaches SQL.
DOMAIN_SOURCES: dict[str, str] = {
    "system": "metric_samples",
    "disk_usage": "disk_usage_samples",
    "disk_io": "disk_io_samples",
    "net": "net_samples",
    "latency": "latency_samples",
}


class _Bucket(BaseModel):
    ts: datetime
    #: Total measured seconds behind this point: the sum of the source rows'
    #: `resolution_seconds`. This is the weight every mean above was divided by.
    sample_weight: int
    sample_count: int


class SystemSeriesPoint(SystemSample, _Bucket):
    temperature_celsius_max: float | None = None


class DiskUsageSeriesPoint(DiskUsageEntry, _Bucket):
    percent_max: float | None = None


class DiskIoSeriesPoint(DiskIoEntry, _Bucket):
    read_bytes_per_s_max: float | None = None
    write_bytes_per_s_max: float | None = None
    busy_percent_max: float | None = None


class NetSeriesPoint(NetEntry, _Bucket):
    tx_bytes_per_s_max: float | None = None
    rx_bytes_per_s_max: float | None = None


class LatencySeriesPoint(LatencyEntry, _Bucket):
    #: Fraction of the bucket's measured time in which the target answered.
    #: `reachable` above is `bool_or` — true if it answered even once — so this
    #: is the field to trust for availability.
    reachable_ratio: float | None = None


class SeriesOut(BaseModel):
    """One response covers every requested domain over one window.

    `source` and `resolution_seconds` are part of the payload rather than an
    implementation detail: a chart drawn from 13-minute buckets must be able to
    say so, and "CPU averaged over 13 minutes" is a different claim from "CPU
    every second".
    """

    device_id: uuid.UUID
    start: datetime
    end: datetime
    source: SourceName
    resolution_seconds: int
    #: True when a domain hit the server's row budget and the tail was dropped.
    #: Ask for a narrower window or fewer points rather than trusting the edge.
    truncated: bool = False

    system: list[SystemSeriesPoint] = Field(default_factory=list)
    disk_usage: list[DiskUsageSeriesPoint] = Field(default_factory=list)
    disk_io: list[DiskIoSeriesPoint] = Field(default_factory=list)
    net: list[NetSeriesPoint] = Field(default_factory=list)
    latency: list[LatencySeriesPoint] = Field(default_factory=list)


POINT_MODELS: dict[str, type[BaseModel]] = {
    "system": SystemSeriesPoint,
    "disk_usage": DiskUsageSeriesPoint,
    "disk_io": DiskIoSeriesPoint,
    "net": NetSeriesPoint,
    "latency": LatencySeriesPoint,
}
