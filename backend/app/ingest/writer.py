"""Batch-write validated samples into the hypertables.

One multi-row INSERT per table per batch rather than per-sample ORM inserts:
a 10s batch from one device touches six tables, and at fleet scale the round
trips dominate.

Writes go through the OWNER session. An agent authenticates as a device, not as
a user, so there is no JWT to derive a tenant GUC from — the user_id stamped on
every row comes from the agent token's own record, which is the authoritative
binding. The composite FK to devices (id, user_id) rejects any row whose
user_id does not match the device's real owner, so a compromised agent still
cannot write into another tenant.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DiskIoSample,
    DiskUsageSample,
    LatencySample,
    MetricSample,
    NetSample,
    ProcessSample,
)
from app.schemas.protocol import (
    MAX_SAMPLE_AGE_SECONDS,
    MAX_SAMPLE_SKEW_SECONDS,
    Sample,
)

logger = logging.getLogger(__name__)


class WriteResult:
    __slots__ = ("accepted", "accepted_samples", "rejected")

    def __init__(self, accepted: int = 0, rejected: int = 0) -> None:
        self.accepted = accepted
        self.rejected = rejected
        #: The Sample objects that actually got written, in order. Separate
        #: from the count because the live pipeline (app/live/bus.py) must
        #: fan out exactly what was durably persisted — a sample rejected for
        #: being outside the time window must never reach a viewer's chart
        #: even though it never reaches the database either.
        self.accepted_samples: list[Sample] = []


def _within_window(ts: datetime, now: datetime) -> bool:
    """Reject samples whose timestamps we should not trust.

    The past window is wide because agents buffer across reconnects. The future
    window is tight: a timestamp ahead of server time is either a broken clock
    or an attempt to poison a forecast, and neither should land in the series.
    """
    if ts > now + timedelta(seconds=MAX_SAMPLE_SKEW_SECONDS):
        return False
    return ts >= now - timedelta(seconds=MAX_SAMPLE_AGE_SECONDS)


async def write_samples(
    session: AsyncSession,
    *,
    device_id: uuid.UUID,
    user_id: uuid.UUID,
    samples: list[Sample],
    now: datetime | None = None,
) -> WriteResult:
    now = now or datetime.now(UTC)
    result = WriteResult()

    system_rows: list[dict] = []
    disk_usage_rows: list[dict] = []
    disk_io_rows: list[dict] = []
    net_rows: list[dict] = []
    latency_rows: list[dict] = []
    process_rows: list[dict] = []

    for sample in samples:
        ts = sample.ts if sample.ts.tzinfo else sample.ts.replace(tzinfo=UTC)
        if not _within_window(ts, now):
            result.rejected += 1
            continue

        base = {
            "device_id": device_id,
            "user_id": user_id,
            "ts": ts,
            "resolution_seconds": sample.resolution_seconds,
        }

        system_rows.append(base | sample.system.model_dump())
        disk_usage_rows.extend(base | e.model_dump() for e in sample.disk_usage)
        disk_io_rows.extend(base | e.model_dump() for e in sample.disk_io)
        net_rows.extend(base | e.model_dump() for e in sample.net)
        latency_rows.extend(base | e.model_dump() for e in sample.latency)
        process_rows.extend(base | e.model_dump() for e in sample.processes)
        result.accepted += 1
        result.accepted_samples.append(sample)

    if result.accepted == 0:
        return result

    # ON CONFLICT DO NOTHING makes redelivery harmless: an agent that reconnects
    # before its ack arrives will resend the batch, and replaying it must not
    # error or double-count.
    for table, rows in (
        (MetricSample, system_rows),
        (DiskUsageSample, disk_usage_rows),
        (DiskIoSample, disk_io_rows),
        (NetSample, net_rows),
        (LatencySample, latency_rows),
        (ProcessSample, process_rows),
    ):
        if rows:
            await session.execute(
                pg_insert(table).on_conflict_do_nothing().values(rows)
            )

    await session.commit()
    return result


__all__ = ["WriteResult", "write_samples"]
