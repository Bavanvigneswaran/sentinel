"""Wire schemas for the dashboard: health scores, per-device summaries, fleet totals.

One request fills the whole command centre. The alternative — a list call plus
one summary call per device — turns a twenty-machine fleet into twenty-one round
trips and makes the page's numbers disagree with each other, because each would
have been read at a slightly different moment. `generated_at` stamps the single
moment they were all read at.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.devices import DeviceOut

Band = Literal["healthy", "degraded", "critical", "unknown"]


class HealthComponentOut(BaseModel):
    key: str
    label: str
    #: None when the platform did not report this metric. The component is then
    #: excluded from the score rather than counted as either good or bad.
    value: float | None
    unit: str
    score: float | None
    weight: int


class HealthOut(BaseModel):
    """0–100, or nothing at all.

    `score` is None for a device that is offline or has reported no measurable
    metric — see app/analysis/health.py. A null score is a real answer and the
    UI must render it as "unknown", never as zero.
    """

    score: int | None
    band: Band
    components: list[HealthComponentOut] = Field(default_factory=list)
    #: Keys of the components that had no reading, for the "not measured here"
    #: line under the score.
    unavailable: list[str] = Field(default_factory=list)
    reason: str | None = None


class MountUsageOut(BaseModel):
    mount: str
    filesystem: str | None = None
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    percent: float | None = None


class LatestReadingsOut(BaseModel):
    """The device's most recent raw sample, for the card's headline numbers.

    Absent entirely (None on the summary) when nothing has landed inside the
    freshness window — a number from twenty minutes ago presented as "current"
    would be the same lie as a synthesised one.
    """

    ts: datetime
    cpu_percent: float | None = None
    mem_percent: float | None = None
    mem_used_bytes: int | None = None
    mem_total_bytes: int | None = None
    swap_percent: float | None = None
    load1: float | None = None
    uptime_seconds: int | None = None
    process_count: int | None = None
    #: Summed across every NIC the agent reported at that timestamp.
    net_rx_bytes_per_s: float | None = None
    net_tx_bytes_per_s: float | None = None
    #: Summed across every physical disk.
    disk_read_bytes_per_s: float | None = None
    disk_write_bytes_per_s: float | None = None
    #: Worst target, not an average: one unreachable link is the story.
    packet_loss_percent: float | None = None
    rtt_ms_avg: float | None = None


class SparklineOut(BaseModel):
    """Columnar rather than a list of objects — three parallel arrays are a
    fraction of the JSON of the equivalent point list, and a fleet page sends
    one of these per device.

    A null inside a value array is a bucket the metric was not measured in. The
    chart must break the line there; joining across it would draw a slope
    through time that was never observed.
    """

    resolution_seconds: int
    ts: list[datetime] = Field(default_factory=list)
    cpu_percent: list[float | None] = Field(default_factory=list)
    mem_percent: list[float | None] = Field(default_factory=list)


class DeviceSummaryOut(BaseModel):
    device: DeviceOut
    health: HealthOut
    latest: LatestReadingsOut | None = None
    #: Fullest mount first. Empty when the device has not reported disk usage.
    disks: list[MountUsageOut] = Field(default_factory=list)
    sparkline: SparklineOut


class FleetTotalsOut(BaseModel):
    devices: int
    online: int
    offline: int
    pending: int
    healthy: int
    degraded: int
    critical: int
    unknown: int


class FleetOverviewOut(BaseModel):
    #: The single instant every number below was read at.
    generated_at: datetime
    #: How far back the sparklines reach.
    window_seconds: int
    totals: FleetTotalsOut
    devices: list[DeviceSummaryOut] = Field(default_factory=list)
