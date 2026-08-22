"""Continuous-aggregate rollups: the one specification both the migration and
the query builder read.

Raw samples are kept 7 days (app/models/metrics.py). Anything older, and any
window wide enough that raw would return tens of thousands of points, is served
from a TimescaleDB continuous aggregate at 1m, 5m or 1h.

Three things here are load-bearing.

**Averages are time-weighted, never avg-of-avg.** A device pushes 10s aggregates
normally and 1s raw while someone watches it live, so the rows inside a bucket
do not represent equal spans of time. Every rollup therefore carries
`sample_weight` — the sum of `resolution_seconds` over the bucket — and every
mean divides by the weight of the rows that actually had a value. Re-aggregating
the 1m rollup into 5m then gives bit-identical numbers to aggregating raw
directly, which is what lets the query builder pick any source it likes.

**Every aggregate expression is valid over raw *and* over a rollup.** A rollup
column is named after the raw column it summarises, so `min(LEAST(cpu_percent,
cpu_percent_min))` means the same thing in the continuous aggregate's own
definition, in a query against raw, and in a query re-bucketing the 1m rollup
into 6h. One expression set, three uses, no chance of the three drifting apart.

**NULL survives the rollup.** `avg`/`sum` skip NULLs and `LEAST`/`GREATEST`
ignore them, so a metric the platform cannot measure rolls up to NULL rather
than to zero — the hard rule in CLAUDE.md applies to derived rows too.

Process samples are deliberately absent: "top 10 processes by CPU" does not have
an average. They live at raw resolution for 7 days and then they are gone.
"""

from __future__ import annotations

from dataclasses import dataclass

#: `{w}` is substituted with the source's per-row weight column — see WEIGHT_*.
WEIGHT_PLACEHOLDER = "{w}"

#: Raw rows weigh whatever span they cover; a rollup row weighs the sum of the
#: spans that went into it.
RAW_WEIGHT = "resolution_seconds"
ROLLUP_WEIGHT = "sample_weight"

#: How many original samples a bucket represents. Over raw that is the row
#: count; over a rollup the rows are already buckets, so counting them would
#: report the number of minutes rather than the number of samples.
RAW_COUNT = "count(*)"
ROLLUP_COUNT = "sum(sample_count)"


def wavg(column: str) -> str:
    """Time-weighted mean, NULL when every row in the bucket was NULL.

    The CASE in the denominator (rather than a FILTER clause) keeps the
    expression legal inside a continuous aggregate definition on every
    TimescaleDB version we care about.
    """
    return (
        f"sum({column} * {{w}}) / "
        f"NULLIF(sum(CASE WHEN {column} IS NULL THEN 0 ELSE {{w}} END), 0)"
    )


def wavg_int(column: str) -> str:
    """Time-weighted mean of an integer column, rounded back to an integer.

    Keeping the wire type identical to the raw column's is what allows one
    Pydantic schema to describe both a raw point and a rolled-up one.
    """
    return f"round({wavg(column)})::bigint"


def lowest(column: str, floor_column: str) -> str:
    """Smallest value seen, across both the mean column and the agent's own
    per-window minimum. LEAST ignores NULLs, so a 1s row (which has no
    `_min`) contributes its plain value and an unavailable metric stays NULL.
    """
    return f"min(LEAST({column}, {floor_column}))"


def highest(column: str, ceil_column: str) -> str:
    return f"max(GREATEST({column}, {ceil_column}))"


@dataclass(frozen=True)
class RollupTier:
    """One materialisation resolution.

    `refresh_start_offset` is deliberately just inside the raw retention window
    rather than a few hours: TimescaleDB only re-materialises ranges its
    invalidation log has marked dirty, so a wide window costs nothing when
    nothing changed but lets the aggregate heal itself after the backend has
    been down for days.

    `refresh_end_offset` is at least one bucket wide so the policy never
    materialises a bucket that is still filling. Real-time aggregation covers
    that trailing edge from raw instead — see MATERIALIZED_ONLY below.
    """

    name: str
    bucket: str
    seconds: int
    retention_days: int
    refresh_schedule: str
    refresh_end_offset: str

    @property
    def retention(self) -> str:
        """The same window as `retention_days`, as a SQL interval literal.

        One number, two uses: the migration's retention policy and the query
        planner's "can this source still answer for that window" check. Two
        independently-maintained constants would eventually disagree and the
        planner would route a query at a source whose chunks had been dropped.
        """
        return f"{self.retention_days} days"


TIERS: tuple[RollupTier, ...] = (
    RollupTier("1m", "1 minute", 60, 30, "5 minutes", "1 minute"),
    RollupTier("5m", "5 minutes", 300, 90, "30 minutes", "10 minutes"),
    RollupTier("1h", "1 hour", 3600, 365, "1 hour", "2 hours"),
)

REFRESH_START_OFFSET = "6 days"

#: Real-time aggregation: the view UNIONs materialised buckets with raw rows
#: newer than the materialisation watermark. Without it the newest few minutes
#: are missing from every rollup and a dashboard sparkline ends in a gap that
#: reads as "this machine just went offline".
#:
#: It must be set at CREATE time. `ALTER MATERIALIZED VIEW ... SET
#: (timescaledb.materialized_only = false)` rebuilds the view over the raw
#: hypertable and is refused while that hypertable has row-level security
#: enabled — the same refusal that shapes migration 0005.
MATERIALIZED_ONLY = False


@dataclass(frozen=True)
class RollupColumn:
    name: str
    #: Aggregate SQL over a source exposing this same set of column names.
    expr: str

    def sql(self, weight: str) -> str:
        return self.expr.replace(WEIGHT_PLACEHOLDER, weight)


@dataclass(frozen=True)
class RollupDomain:
    """One measurement domain and the entity it is measured against."""

    source: str
    #: Columns identifying the sub-entity (a mount, a NIC, a target). Empty for
    #: metric_samples, which is one row per device per timestamp.
    entity_keys: tuple[str, ...]
    columns: tuple[RollupColumn, ...]

    def cagg_name(self, tier: RollupTier) -> str:
        """The continuous aggregate itself. The app role has no access to it —
        it is reachable only through `view_name()`."""
        return f"cagg_{self.source}_{tier.name}"

    def view_name(self, tier: RollupTier) -> str:
        """The tenant-scoped wrapper the application queries."""
        return self.view_name_for(tier.name)

    def view_name_for(self, tier_name: str) -> str:
        """As `view_name`, for a caller holding only the tier's name.

        Raises rather than composing a name for a tier that does not exist: the
        result is interpolated into SQL, so an unrecognised tier must fail here
        and not become an identifier further down.
        """
        if tier_name not in TIERS_BY_NAME:
            raise KeyError(f"unknown rollup tier: {tier_name!r}")
        return f"{self.source}_{tier_name}"


def _c(name: str, expr: str) -> RollupColumn:
    return RollupColumn(name, expr)


def bookkeeping(*, count: str) -> tuple[RollupColumn, ...]:
    """The two columns present on every rollup.

    `sample_weight` is what makes re-aggregation exact; `sample_count` is how
    many original samples the bucket represents, which is the only honest way to
    tell a quiet minute from a minute the device spent disconnected.

    Both accumulate rather than recount, which is why `count` is a parameter:
    summing a rollup's `sample_count` preserves the original sample total, while
    `count(*)` over the same rows would report how many buckets were merged.
    """
    return (
        _c("sample_weight", "sum({w})::bigint"),
        _c("sample_count", f"({count})::bigint"),
    )


SYSTEM = RollupDomain(
    source="metric_samples",
    entity_keys=(),
    columns=(
        _c("cpu_percent", wavg("cpu_percent")),
        _c("cpu_percent_min", lowest("cpu_percent", "cpu_percent_min")),
        _c("cpu_percent_max", highest("cpu_percent", "cpu_percent_max")),
        _c("cpu_user_percent", wavg("cpu_user_percent")),
        _c("cpu_system_percent", wavg("cpu_system_percent")),
        _c("cpu_iowait_percent", wavg("cpu_iowait_percent")),
        _c("cpu_freq_mhz", wavg("cpu_freq_mhz")),
        _c("ctx_switches_per_s", wavg("ctx_switches_per_s")),
        _c("load1", wavg("load1")),
        _c("load5", wavg("load5")),
        _c("load15", wavg("load15")),
        # Capacity, not a gauge: max() over a bucket is the machine's RAM, and
        # averaging it would only blur the one moment someone added a stick.
        _c("mem_total_bytes", "max(mem_total_bytes)"),
        _c("mem_used_bytes", wavg_int("mem_used_bytes")),
        _c("mem_available_bytes", wavg_int("mem_available_bytes")),
        _c("mem_percent", wavg("mem_percent")),
        _c("mem_percent_min", lowest("mem_percent", "mem_percent_min")),
        _c("mem_percent_max", highest("mem_percent", "mem_percent_max")),
        _c("swap_total_bytes", "max(swap_total_bytes)"),
        _c("swap_used_bytes", wavg_int("swap_used_bytes")),
        _c("swap_percent", wavg("swap_percent")),
        # Monotonic within a bucket, so max() is the value at the bucket's end.
        # A reboot mid-bucket shows the post-reboot uptime, which is the more
        # useful of the two readings.
        _c("uptime_seconds", "max(uptime_seconds)"),
        _c("logged_in_users", wavg_int("logged_in_users")),
        _c("process_count", wavg_int("process_count")),
        _c("active_connections", wavg_int("active_connections")),
        _c("temperature_celsius", wavg("temperature_celsius")),
        _c("temperature_celsius_max", "max(temperature_celsius)"),
        _c("fan_rpm", wavg("fan_rpm")),
        _c("battery_percent", wavg("battery_percent")),
        # "Was it on mains at any point in this bucket". Averaging a boolean
        # would invent a state the machine was never in.
        _c("battery_plugged", "bool_or(battery_plugged)"),
    ),
)


DISK_USAGE = RollupDomain(
    source="disk_usage_samples",
    entity_keys=("mount",),
    columns=(
        # Constant per mount in practice; max() picks deterministically if a
        # mount point is ever reused for a different filesystem.
        _c("filesystem", "max(filesystem)"),
        _c("total_bytes", "max(total_bytes)"),
        _c("used_bytes", wavg_int("used_bytes")),
        _c("free_bytes", wavg_int("free_bytes")),
        _c("percent", wavg("percent")),
        _c("percent_max", "max(percent)"),
    ),
)


DISK_IO = RollupDomain(
    source="disk_io_samples",
    entity_keys=("disk",),
    columns=(
        _c("read_bytes_per_s", wavg("read_bytes_per_s")),
        _c("read_bytes_per_s_max", "max(read_bytes_per_s)"),
        _c("write_bytes_per_s", wavg("write_bytes_per_s")),
        _c("write_bytes_per_s_max", "max(write_bytes_per_s)"),
        _c("read_iops", wavg("read_iops")),
        _c("write_iops", wavg("write_iops")),
        _c("busy_percent", wavg("busy_percent")),
        _c("busy_percent_max", "max(busy_percent)"),
    ),
)


NET = RollupDomain(
    source="net_samples",
    entity_keys=("nic",),
    columns=(
        _c("tx_bytes_per_s", wavg("tx_bytes_per_s")),
        _c("tx_bytes_per_s_max", "max(tx_bytes_per_s)"),
        _c("rx_bytes_per_s", wavg("rx_bytes_per_s")),
        _c("rx_bytes_per_s_max", "max(rx_bytes_per_s)"),
        _c("tx_packets_per_s", wavg("tx_packets_per_s")),
        _c("rx_packets_per_s", wavg("rx_packets_per_s")),
        _c("errors_in_per_s", wavg("errors_in_per_s")),
        _c("errors_out_per_s", wavg("errors_out_per_s")),
        _c("drops_in_per_s", wavg("drops_in_per_s")),
        _c("drops_out_per_s", wavg("drops_out_per_s")),
    ),
)


LATENCY = RollupDomain(
    source="latency_samples",
    entity_keys=("target",),
    columns=(
        _c("rtt_ms_avg", wavg("rtt_ms_avg")),
        _c("rtt_ms_min", lowest("rtt_ms_avg", "rtt_ms_min")),
        _c("rtt_ms_max", highest("rtt_ms_avg", "rtt_ms_max")),
        _c("packet_loss_percent", wavg("packet_loss_percent")),
        # "Did it answer at all in this bucket" — the optimistic reading, kept
        # only so the column shape matches raw.
        _c("reachable", "bool_or(reachable)"),
        # The honest one: the fraction of the bucket's measured time in which
        # the target answered. This is what Phase 9's availability stats want.
        _c(
            "reachable_ratio",
            "sum(CASE WHEN reachable THEN {w} ELSE 0 END)::double precision "
            "/ NULLIF(sum({w}), 0)",
        ),
    ),
)


DOMAINS: tuple[RollupDomain, ...] = (SYSTEM, DISK_USAGE, DISK_IO, NET, LATENCY)

DOMAINS_BY_SOURCE: dict[str, RollupDomain] = {d.source: d for d in DOMAINS}
TIERS_BY_NAME: dict[str, RollupTier] = {t.name: t for t in TIERS}


def select_list(domain: RollupDomain, *, weight: str, count: str) -> list[str]:
    """`<expr> AS <name>` for every aggregate column plus the bookkeeping pair.

    `weight` and `count` describe the *source* being read: pass RAW_WEIGHT /
    RAW_COUNT over a hypertable, ROLLUP_WEIGHT / ROLLUP_COUNT over a rollup.
    Everything else is identical either way, which is the property that lets the
    query planner swap sources without changing the answer.
    """
    return [
        f"{column.sql(weight)} AS {column.name}"
        for column in (*domain.columns, *bookkeeping(count=count))
    ]


def value_columns(domain: RollupDomain) -> tuple[str, ...]:
    return tuple(column.name for column in domain.columns)
