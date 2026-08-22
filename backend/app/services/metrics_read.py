"""Reading the current value of a metric, shared by anything that needs "the
newest reading per (device[, entity]) inside a freshness window" — originally
written for the dashboard (app/services/fleet_service.py) and reused as-is by
the alert evaluator (app/alerts/evaluator.py) so the two never disagree about
what "current" means.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

#: A reading older than this is not "current". Nine normal push intervals —
#: wide enough to survive a slow push, narrow enough that a stalled agent stops
#: contributing numbers to a live dashboard.
FRESH_WINDOW_SECONDS = 90


def _device_filter(device_ids: list[uuid.UUID] | None) -> str:
    return " AND device_id = ANY(:device_ids)" if device_ids else ""


async def latest_per_entity(
    session: AsyncSession,
    *,
    table: str,
    entity_keys: tuple[str, ...],
    columns: tuple[str, ...],
    since: datetime,
    device_ids: list[uuid.UUID] | None,
) -> list[dict[str, Any]]:
    """The newest row per (device, entity) inside the window.

    DISTINCT ON rather than a window function: on a hypertable ordered by ts
    descending this stops at the first row of each group, and the alternative
    (rank() over a partition) materialises every row in the window first.

    Table and column names come from this module's own constants; only values
    are bound.
    """
    keys = ", ".join(("device_id", *entity_keys))
    selected = ", ".join(("device_id", *entity_keys, "ts", *columns))
    rows = await session.execute(
        sa.text(
            f"SELECT DISTINCT ON ({keys}) {selected} "  # noqa: S608 — fixed identifiers
            f"FROM {table} WHERE ts >= :since{_device_filter(device_ids)} "
            f"ORDER BY {keys}, ts DESC"
        ),
        {"since": since, "device_ids": device_ids},
    )
    return [dict(row) for row in rows.mappings()]
