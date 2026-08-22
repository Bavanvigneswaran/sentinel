"""The time-range query API's engine: pick a source, then bucket it.

A request asks for a window and a point budget, not for a resolution. This
module decides which of the four sources — raw, or the 1m/5m/1h continuous
aggregates — can answer, and at what bucket width, then runs one query per
requested domain.

The choice is honest in both directions: the response always reports the source
and the bucket width it actually used, because "CPU averaged over 13 minutes"
and "CPU sampled every second" are different claims about the same machine and
the chart has to be able to say which one it is drawing.

Two properties make the source interchangeable:

* every rollup carries `sample_weight`, so re-bucketing a rollup into a wider
  bucket is a weighted mean and lands on exactly the number aggregating raw
  would have produced (verified in tests/test_rollup_math.py);
* every rollup column is named after the raw column it summarises, so one
  aggregate expression from app/models/rollups.py serves all four sources.

Tenancy: raw tables are RLS-scoped; the rollups are reached only through their
`security_barrier` wrapper views, which carry the same predicate. Neither is the
caller's responsibility, and the caller cannot name the unscoped aggregate at
all — it has no privilege on it. The explicit `device_id` filter here is a
selection, not a security boundary.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metrics import DEFAULT_PUSH_RESOLUTION_SECONDS, RAW_RETENTION_DAYS
from app.models.rollups import (
    DOMAINS,
    RAW_COUNT,
    RAW_WEIGHT,
    ROLLUP_COUNT,
    ROLLUP_WEIGHT,
    TIERS,
    RollupDomain,
    select_list,
)

#: Roughly one point per two horizontal pixels on a wide chart. The client may
#: ask for fewer; the cap exists because the cost of a query is the row count it
#: returns, not the window it covers.
DEFAULT_MAX_POINTS = 720
MIN_MAX_POINTS = 10
MAX_MAX_POINTS = 5_000

#: A device rarely has more than a handful of mounts, NICs or ping targets, but
#: the row budget for a multi-entity domain has to allow for several series at
#: full length. The agent itself caps entities per sample at 64.
MAX_ENTITIES = 24

#: Widest window a single request may ask for. Two years is already past the 1h
#: rollup's retention; beyond that a request is a mistake, not a query.
MAX_WINDOW_DAYS = 730


@dataclass(frozen=True)
class SeriesSource:
    """One place a series can be read from."""

    name: str
    #: The finest bucket this source can produce. For raw it is the agent's
    #: default push interval — live windows are denser, but planning on the
    #: optimistic figure would under-bucket an ordinary device.
    resolution_seconds: int
    weight_column: str
    #: How to total the sample count. Over a rollup the rows are already
    #: buckets, so this sums rather than counts — see rollups.bookkeeping().
    count_expr: str
    retention_seconds: int

    def table(self, domain: RollupDomain) -> str:
        return domain.source if self.name == "raw" else domain.view_name_for(self.name)


RAW_SOURCE = SeriesSource(
    name="raw",
    resolution_seconds=DEFAULT_PUSH_RESOLUTION_SECONDS,
    weight_column=RAW_WEIGHT,
    count_expr=RAW_COUNT,
    retention_seconds=RAW_RETENTION_DAYS * 86_400,
)

#: Ordered finest to coarsest. The planner walks this list, so the order is
#: part of the contract, not a coincidence of construction.
SOURCES: tuple[SeriesSource, ...] = (
    RAW_SOURCE,
    *(
        SeriesSource(
            name=tier.name,
            resolution_seconds=tier.seconds,
            weight_column=ROLLUP_WEIGHT,
            count_expr=ROLLUP_COUNT,
            retention_seconds=tier.retention_days * 86_400,
        )
        for tier in TIERS
    ),
)


class WindowTooWide(ValueError):
    """The requested range exceeds MAX_WINDOW_DAYS."""


@dataclass(frozen=True)
class SeriesPlan:
    """What a request resolved to, before any row is read."""

    source: SeriesSource
    bucket_seconds: int
    start: datetime
    end: datetime

    @property
    def estimated_points(self) -> int:
        span = (self.end - self.start).total_seconds()
        return max(1, math.ceil(span / self.bucket_seconds))


def plan_series(
    start: datetime,
    end: datetime,
    *,
    max_points: int = DEFAULT_MAX_POINTS,
    now: datetime | None = None,
) -> SeriesPlan:
    """Choose the cheapest source that can still answer at the needed density.

    Two constraints, applied in this order:

    1. **Retention.** A source whose oldest surviving chunk is newer than
       `start` cannot answer — raw is gone after 7 days no matter how narrow the
       window is. Sources that cannot cover the window are dropped outright.
    2. **Density.** The bucket has to be at least `span / max_points` wide, and
       a source cannot produce a bucket finer than its own resolution. Among the
       sources that can, take the *coarsest* — it holds the fewest rows for the
       same answer.

    The bucket is then snapped up to a whole multiple of the chosen source's
    resolution. That snap is what keeps the arithmetic exact: a bucket that
    split a source row would be averaging a value across a boundary it was never
    measured over.
    """
    now = now or datetime.now(UTC)
    if end <= start:
        raise ValueError("end must be after start")
    span = (end - start).total_seconds()
    if span > MAX_WINDOW_DAYS * 86_400:
        raise WindowTooWide(f"range exceeds {MAX_WINDOW_DAYS} days")

    max_points = max(MIN_MAX_POINTS, min(MAX_MAX_POINTS, max_points))
    min_bucket = max(1, math.ceil(span / max_points))

    covering = [s for s in SOURCES if start >= now - timedelta(seconds=s.retention_seconds)]
    if not covering:
        # Older than every retention window. The coarsest source is the only one
        # with any chance of holding something; it will simply return less.
        covering = [SOURCES[-1]]

    dense_enough = [s for s in covering if s.resolution_seconds <= min_bucket]
    # Coarsest source that can still hit the requested density, else the finest
    # source that survives retention (the window is narrower than one bucket of
    # anything coarser).
    source = dense_enough[-1] if dense_enough else covering[0]

    buckets = max(1, math.ceil(min_bucket / source.resolution_seconds))
    return SeriesPlan(
        source=source,
        bucket_seconds=buckets * source.resolution_seconds,
        start=start,
        end=end,
    )


def _row_budget(domain: RollupDomain, plan: SeriesPlan) -> int:
    """Rows this request may return before it is reported as truncated.

    The +2 absorbs bucket alignment: `time_bucket` snaps to the epoch, so a
    window that does not begin on a bucket boundary spans one more bucket than
    its width implies. Without the slack an ordinary request would flag itself
    truncated when it had returned every row that exists.
    """
    per_entity = plan.estimated_points + 2
    return per_entity * (MAX_ENTITIES if domain.entity_keys else 1) + 1


def _build_query(domain: RollupDomain, plan: SeriesPlan) -> sa.TextClause:
    """One statement, built from the frozen spec in app/models/rollups.py.

    Every identifier interpolated here comes from that spec — never from the
    request — and the domain is looked up by identity before this is called.
    Only values are bound.
    """
    keys = list(domain.entity_keys)
    bucket = "time_bucket(make_interval(secs => :bucket), ts)"
    columns = [
        f"{bucket} AS ts",
        *keys,
        *select_list(
            domain, weight=plan.source.weight_column, count=plan.source.count_expr
        ),
    ]
    order = ", ".join(["ts", *keys])
    return sa.text(
        f"SELECT {', '.join(columns)} "  # noqa: S608 — identifiers are from the spec
        f"FROM {plan.source.table(domain)} "
        "WHERE device_id = :device_id AND ts >= :start AND ts < :end "
        f"GROUP BY {', '.join([bucket, *keys])} "
        f"ORDER BY {order} "
        "LIMIT :row_limit"
    )


async def fetch_series(
    session: AsyncSession,
    *,
    device_id: uuid.UUID,
    domain: RollupDomain,
    plan: SeriesPlan,
) -> tuple[list[dict[str, Any]], bool]:
    """Rows for one domain, plus whether the row budget cut the result short."""
    if domain not in DOMAINS:
        raise ValueError(f"unknown rollup domain: {domain.source}")

    budget = _row_budget(domain, plan)
    result = await session.execute(
        _build_query(domain, plan),
        {
            "bucket": plan.bucket_seconds,
            "device_id": device_id,
            "start": plan.start,
            "end": plan.end,
            "row_limit": budget,
        },
    )
    rows = [dict(row) for row in result.mappings()]
    truncated = len(rows) >= budget
    return rows[: budget - 1] if truncated else rows, truncated
