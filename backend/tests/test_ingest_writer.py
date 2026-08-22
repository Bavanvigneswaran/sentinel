"""Sample validation and batch writes."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.ingest.writer import write_samples
from app.models import User
from app.schemas.protocol import (
    DiskUsageEntry,
    LatencyEntry,
    NetEntry,
    ProcessEntry,
    Sample,
    SystemSample,
)
from app.services import enrollment_service as svc


@pytest.fixture
async def device(admin_session):
    user = User(email="agent@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    dev = await svc.register_device(admin_session, user_id=user.id, name="mac")
    return dev


def _sample(ts: datetime | None = None, cpu: float = 12.5) -> Sample:
    return Sample(
        ts=ts or datetime.now(UTC),
        resolution_seconds=10,
        system=SystemSample(cpu_percent=cpu, mem_percent=50.0, mem_used_bytes=1234),
        disk_usage=[DiskUsageEntry(mount="/", percent=42.0, used_bytes=100)],
        net=[NetEntry(nic="en0", rx_bytes_per_s=1000.0)],
        latency=[LatencyEntry(target="1.1.1.1:443", reachable=True, rtt_ms_avg=5.5)],
        processes=[ProcessEntry(rank_by="cpu", rank=1, pid=1, name="init", cpu_percent=1.0)],
    )


async def test_a_batch_lands_in_every_table(admin_session, device):
    result = await write_samples(
        admin_session, device_id=device.id, user_id=device.user_id, samples=[_sample()]
    )
    assert result.accepted == 1
    assert result.rejected == 0

    for table in ("metric_samples", "disk_usage_samples", "net_samples", "latency_samples"):
        count = await admin_session.scalar(sa.text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        assert count == 1, f"{table} did not receive the sample"


async def test_values_round_trip_intact(admin_session, device):
    await write_samples(
        admin_session, device_id=device.id, user_id=device.user_id, samples=[_sample(cpu=73.25)]
    )
    row = (
        await admin_session.execute(
            sa.text("SELECT cpu_percent, mem_percent, resolution_seconds FROM metric_samples")
        )
    ).one()
    assert row.cpu_percent == pytest.approx(73.25)
    assert row.mem_percent == pytest.approx(50.0)
    assert row.resolution_seconds == 10


async def test_samples_from_the_future_are_rejected(admin_session, device):
    """A timestamp ahead of server time is a broken clock or an attempt to
    poison a forecast. Neither belongs in the series."""
    future = datetime.now(UTC) + timedelta(hours=1)
    result = await write_samples(
        admin_session, device_id=device.id, user_id=device.user_id, samples=[_sample(future)]
    )
    assert result.accepted == 0
    assert result.rejected == 1
    assert await admin_session.scalar(sa.text("SELECT count(*) FROM metric_samples")) == 0


async def test_very_old_samples_are_rejected(admin_session, device):
    old = datetime.now(UTC) - timedelta(days=2)
    result = await write_samples(
        admin_session, device_id=device.id, user_id=device.user_id, samples=[_sample(old)]
    )
    assert result.rejected == 1


async def test_a_buffered_backlog_within_the_window_is_accepted(admin_session, device):
    """Agents buffer across an outage, so recent-but-not-current samples are
    normal and must not be dropped."""
    now = datetime.now(UTC)
    samples = [_sample(now - timedelta(minutes=m)) for m in range(0, 30)]
    result = await write_samples(
        admin_session, device_id=device.id, user_id=device.user_id, samples=samples
    )
    assert result.accepted == 30
    assert result.rejected == 0


async def test_mixed_batches_accept_the_good_and_reject_the_bad(admin_session, device):
    samples = [_sample(), _sample(datetime.now(UTC) + timedelta(hours=5)), _sample()]
    result = await write_samples(
        admin_session, device_id=device.id, user_id=device.user_id, samples=samples
    )
    assert (result.accepted, result.rejected) == (2, 1)


async def test_redelivery_is_idempotent(admin_session, device):
    """An agent that reconnects before its ack arrives resends the batch.
    Replaying it must not error or double-count."""
    sample = _sample()
    first = await write_samples(
        admin_session, device_id=device.id, user_id=device.user_id, samples=[sample]
    )
    second = await write_samples(
        admin_session, device_id=device.id, user_id=device.user_id, samples=[sample]
    )
    assert first.accepted == second.accepted == 1
    assert await admin_session.scalar(sa.text("SELECT count(*) FROM metric_samples")) == 1


async def test_unavailable_metrics_are_stored_as_null_not_zero(admin_session, device):
    """A metric the platform cannot measure must read as unavailable, never as
    a real zero."""
    sample = Sample(
        ts=datetime.now(UTC),
        resolution_seconds=10,
        system=SystemSample(cpu_percent=5.0),  # everything else omitted
    )
    await write_samples(
        admin_session, device_id=device.id, user_id=device.user_id, samples=[sample]
    )
    row = (
        await admin_session.execute(
            sa.text("SELECT load1, temperature_celsius, battery_percent FROM metric_samples")
        )
    ).one()
    assert row.load1 is None
    assert row.temperature_celsius is None
    assert row.battery_percent is None
