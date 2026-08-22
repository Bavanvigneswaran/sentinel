"""The continuous aggregates must agree with the raw data they summarise.

This is the property the whole time-range API rests on: because every rollup
carries `sample_weight`, re-bucketing a rollup gives the same number as
aggregating raw over the same window, and the query planner is therefore free to
pick whichever source is cheapest. If this file goes red, the planner is
silently changing the answer depending on how wide the window happened to be.

The aggregates are created with real-time aggregation on, so rows written here
are visible through the rollup immediately — no refresh needed for most of these.
One test refreshes explicitly, to cover the materialised half as well.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.db import AdminSessionLocal
from app.ingest.writer import write_samples
from app.models import User
from app.schemas.protocol import LatencyEntry, Sample, SystemSample
from app.services import enrollment_service as svc
from tests.conftest import scoped_session_for

#: Minute-aligned so a bucket boundary is predictable, and recent enough that
#: write_samples accepts it — the writer drops anything older than
#: MAX_SAMPLE_AGE_SECONDS, exactly as it would for a real agent's backlog.
BASE = (datetime.now(UTC) - timedelta(minutes=20)).replace(second=0, microsecond=0)


@pytest.fixture
async def device(admin_session):
    user = User(email="rollup-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    dev = await svc.register_device(admin_session, user_id=user.id, name="rollup-box")
    return {"user": user, "device": dev}


async def _write(device, samples: list[Sample]) -> None:
    async with AdminSessionLocal() as session:
        await write_samples(
            session,
            device_id=device["device"].id,
            user_id=device["user"].id,
            samples=samples,
        )


def _system(ts: datetime, resolution: int, **fields) -> Sample:
    return Sample(ts=ts, resolution_seconds=resolution, system=SystemSample(**fields))


async def test_weighted_mean_favours_the_longer_sample(device):
    """Two 1s samples at 100% and one 10s sample at 0% is not 66.7%.

    The 10s sample covers ten times as much wall-clock time, so the minute
    averaged 100*2/12 = 16.67%. A plain avg() would report the machine as four
    times busier than it was.
    """
    await _write(
        device,
        [
            _system(BASE + timedelta(seconds=1), 1, cpu_percent=100.0),
            _system(BASE + timedelta(seconds=2), 1, cpu_percent=100.0),
            _system(BASE + timedelta(seconds=20), 10, cpu_percent=0.0),
        ],
    )

    async with scoped_session_for(device["user"].id) as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT cpu_percent, sample_weight, sample_count "
                    "FROM metric_samples_1m WHERE device_id = :d AND ts = :ts"
                ),
                {"d": device["device"].id, "ts": BASE},
            )
        ).mappings().one()

    assert row["cpu_percent"] == pytest.approx(100 * 2 / 12)
    assert row["sample_weight"] == 12
    assert row["sample_count"] == 3


async def test_rebucketing_a_rollup_equals_aggregating_raw(device):
    """The 1m rollup rolled up to 5m must equal raw rolled up to 5m directly."""
    # Spread across four 1m buckets so the 5m re-aggregation has something to
    # combine, with uneven sample counts per bucket so an unweighted mean would
    # visibly disagree.
    await _write(
        device,
        [
            _system(BASE + timedelta(seconds=offset), 10, cpu_percent=value)
            for offset, value in (
                (5, 5.0), (15, 40.0), (25, 12.5),
                (65, 90.0),
                (125, 3.0), (135, 60.0), (145, 22.0),
                (185, 71.0),
            )
        ],
    )

    rebucket = (
        "sum(cpu_percent * {w}) / "
        "NULLIF(sum(CASE WHEN cpu_percent IS NULL THEN 0 ELSE {w} END), 0)"
    )
    async with scoped_session_for(device["user"].id) as session:
        from_rollup = await session.scalar(
            sa.text(
                f"SELECT {rebucket.format(w='sample_weight')} "  # noqa: S608
                "FROM metric_samples_1m WHERE device_id = :d"
            ),
            {"d": device["device"].id},
        )
        from_raw = await session.scalar(
            sa.text(
                f"SELECT {rebucket.format(w='resolution_seconds')} "  # noqa: S608
                "FROM metric_samples WHERE device_id = :d"
            ),
            {"d": device["device"].id},
        )

    assert from_rollup == pytest.approx(from_raw)


async def test_an_unmeasurable_metric_rolls_up_to_null_not_zero(device):
    """CLAUDE.md's hard rule applies to derived rows too. macOS reports no
    iowait; the rollup must say "unavailable", not "0% iowait"."""
    await _write(
        device,
        [
            _system(BASE + timedelta(seconds=5), 10, cpu_percent=20.0),
            _system(BASE + timedelta(seconds=15), 10, cpu_percent=30.0),
        ],
    )

    async with scoped_session_for(device["user"].id) as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT cpu_iowait_percent, load1, temperature_celsius, cpu_percent "
                    "FROM metric_samples_1m WHERE device_id = :d"
                ),
                {"d": device["device"].id},
            )
        ).mappings().one()

    assert row["cpu_iowait_percent"] is None
    assert row["load1"] is None
    assert row["temperature_celsius"] is None
    assert row["cpu_percent"] == pytest.approx(25.0)


async def test_min_and_max_span_both_the_mean_and_the_agents_own_extremes(device):
    """A 10s row carries the agent's per-window min/max; a 1s row carries only
    its value. LEAST/GREATEST ignore NULL, so both kinds contribute."""
    await _write(
        device,
        [
            _system(BASE + timedelta(seconds=5), 10, cpu_percent=50.0,
                    cpu_percent_min=11.0, cpu_percent_max=88.0),
            _system(BASE + timedelta(seconds=30), 1, cpu_percent=95.0),
            _system(BASE + timedelta(seconds=31), 1, cpu_percent=4.0),
        ],
    )

    async with scoped_session_for(device["user"].id) as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT cpu_percent_min, cpu_percent_max "
                    "FROM metric_samples_1m WHERE device_id = :d"
                ),
                {"d": device["device"].id},
            )
        ).mappings().one()

    assert row["cpu_percent_min"] == pytest.approx(4.0)
    assert row["cpu_percent_max"] == pytest.approx(95.0)


async def test_latency_reachable_ratio_is_time_weighted(device):
    """`reachable` is bool_or — true if the target answered even once — so the
    ratio is the field that tells you the link was down for most of the minute."""
    await _write(
        device,
        [
            Sample(
                ts=BASE + timedelta(seconds=offset),
                resolution_seconds=10,
                system=SystemSample(),
                latency=[LatencyEntry(target="1.1.1.1", reachable=up)],
            )
            for offset, up in ((5, True), (15, False), (25, False), (35, False))
        ],
    )

    async with scoped_session_for(device["user"].id) as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT reachable, reachable_ratio FROM latency_samples_1m "
                    "WHERE device_id = :d"
                ),
                {"d": device["device"].id},
            )
        ).mappings().one()

    assert row["reachable"] is True
    assert row["reachable_ratio"] == pytest.approx(0.25)


async def test_materialised_rows_agree_with_the_realtime_view(device, admin_engine):
    """Everything above reads through real-time aggregation, which unions raw
    rows the aggregate has not stored yet. Refreshing moves those rows into the
    materialisation hypertable; the answer must not change when it does."""
    await _write(
        device,
        [
            _system(BASE + timedelta(seconds=offset), 10, cpu_percent=value)
            for offset, value in ((5, 10.0), (15, 20.0), (25, 60.0))
        ],
    )

    async with scoped_session_for(device["user"].id) as session:
        before = await session.scalar(
            sa.text("SELECT cpu_percent FROM metric_samples_1m WHERE device_id = :d"),
            {"d": device["device"].id},
        )

    # refresh_continuous_aggregate cannot run inside a transaction block.
    async with admin_engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(
            # asyncpg cannot infer a parameter type inside CALL, so the casts
            # are load-bearing rather than decorative.
            sa.text(
                "CALL refresh_continuous_aggregate('cagg_metric_samples_1m', "
                "CAST(:lo AS timestamptz), CAST(:hi AS timestamptz))"
            ),
            {"lo": BASE - timedelta(hours=1), "hi": BASE + timedelta(hours=1)},
        )

    async with scoped_session_for(device["user"].id) as session:
        after = await session.scalar(
            sa.text("SELECT cpu_percent FROM metric_samples_1m WHERE device_id = :d"),
            {"d": device["device"].id},
        )

    assert before == pytest.approx(30.0)
    assert after == pytest.approx(before)
