"""An Android device reports a different metric set — and must be scored, and
rendered, as itself rather than as a Linux box with broken sensors.

Phase 10b's Kotlin collector sends what a phone can honestly measure and null
for the rest (docs/ANDROID_METRICS.md). Nothing in the backend was changed to
accommodate that — `Device.platform` already carried "android" and every metric
column was already nullable — so these tests exist to prove the claim rather
than to cover new code: that the existing health score, freshness rules and
summary payload do the right thing with a sample shaped like a phone's.

The sample below is deliberately *exactly* what `SystemCollector.collectSystem()`
produces: no CPU of any kind, no process count, no disk IO, no load average, and
real memory / storage / battery / temperature / packet loss.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.analysis.health import HealthInputs, compute_health
from app.db import AdminSessionLocal
from app.ingest.writer import write_samples
from app.models import Device, User
from app.schemas.protocol import (
    DiskUsageEntry,
    HostInfo,
    LatencyEntry,
    NetEntry,
    Sample,
    SystemSample,
)
from app.security.tokens import issue_access_token
from app.services import enrollment_service as svc


def _headers(user_id) -> dict:
    token, _ = issue_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _android_system() -> SystemSample:
    """The exact field set the Kotlin collector fills in."""
    return SystemSample(
        # Not measurable: /proc/stat is denied to apps from API 26, and the
        # collector never even attempts it.
        cpu_percent=None,
        cpu_user_percent=None,
        cpu_system_percent=None,
        cpu_iowait_percent=None,
        ctx_switches_per_s=None,
        load1=None,
        # Measurable: ActivityManager.MemoryInfo, device-wide and exact.
        mem_total_bytes=8_000_000_000,
        mem_used_bytes=4_400_000_000,
        mem_available_bytes=3_600_000_000,
        mem_percent=55.0,
        # zram, read from /proc/meminfo where the device allows it.
        swap_percent=12.0,
        uptime_seconds=86_400,
        # Not measurable: no process enumeration, no /proc/net, no fan.
        process_count=None,
        active_connections=None,
        logged_in_users=None,
        fan_rpm=None,
        # Measurable, and the metrics a desktop does *not* have.
        battery_percent=76.0,
        battery_plugged=False,
        temperature_celsius=31.4,
    )


@pytest.fixture
async def phone(admin_session):
    """An enrolled Android device with one recent, phone-shaped sample."""
    user = User(email="android-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()

    device = await svc.register_device(
        admin_session, user_id=user.id, name="pixel", platform="android"
    )

    now = datetime.now(UTC)
    async with AdminSessionLocal() as session:
        await write_samples(
            session,
            device_id=device.id,
            user_id=user.id,
            samples=[
                Sample(
                    ts=now - timedelta(seconds=offset),
                    resolution_seconds=10,
                    system=_android_system(),
                    disk_usage=[
                        DiskUsageEntry(
                            mount="/data",
                            filesystem=None,
                            total_bytes=100_000_000_000,
                            used_bytes=64_000_000_000,
                            free_bytes=36_000_000_000,
                            percent=64.0,
                        )
                    ],
                    # No per-block-device counters exist on Android at all.
                    disk_io=[],
                    net=[
                        NetEntry(
                            nic="device-total",
                            rx_bytes_per_s=12_000.0,
                            tx_bytes_per_s=3_000.0,
                            errors_in_per_s=None,
                            drops_in_per_s=None,
                        )
                    ],
                    latency=[
                        LatencyEntry(
                            target="1.1.1.1:443",
                            reachable=True,
                            rtt_ms_avg=28.0,
                            packet_loss_percent=0.0,
                        )
                    ],
                    # Fillable with one entry — this process — under a heading
                    # that means "top N on this machine". Deliberately empty.
                    processes=[],
                )
                for offset in (10, 20, 30)
            ],
        )
        await session.execute(
            sa.update(Device)
            .where(Device.id == device.id)
            .values(status="online", last_seen_at=sa.func.now())
        )
        await session.commit()

    return {"user": user, "device": device}


def test_the_protocol_accepts_an_android_host_without_a_version_bump():
    """Phase 10b added no protocol field, so PROTOCOL_VERSION stays at 1.

    If this ever fails, either the Kotlin collector needs a field the schema
    does not have, or somebody bumped one side without the other — and the
    server refuses a mismatch at handshake.
    """
    from app.schemas.protocol import PROTOCOL_VERSION

    assert PROTOCOL_VERSION == 1

    host = HostInfo(
        hostname="Pixel 9",
        os="Android",
        os_version="16",
        kernel_version="6.1.0-android15",
        arch="arm64-v8a",
        cpu_cores=8,
        total_memory_bytes=8_000_000_000,
        agent_version="0.1.0",
        platform="android",
    )
    assert host.platform == "android"


def test_a_phone_is_scored_on_what_it_measured_with_the_weights_renormalised():
    """The arithmetic, before any database is involved.

    Android reports no CPU and no iowait, so those components are excluded and
    the remaining weights are renormalised — a phone at 55% memory and 64% disk
    must land in the same band a Linux box with those numbers would, not be
    dragged down by two components it can never fill in.
    """
    phone_health = compute_health(
        HealthInputs(
            cpu_percent=None,
            memory_percent=55.0,
            swap_percent=12.0,
            disk_percent=64.0,
            packet_loss_percent=0.0,
            cpu_iowait_percent=None,
        )
    )

    assert phone_health.score is not None
    assert phone_health.band != "unknown"
    assert set(phone_health.unavailable) == {"cpu", "iowait"}

    # The renormalisation is the point: a desktop reporting the same four
    # metrics *plus* a healthy CPU and iowait would not score wildly
    # differently, and the phone is certainly not penalised for the gap.
    desktop_health = compute_health(
        HealthInputs(
            cpu_percent=20.0,
            memory_percent=55.0,
            swap_percent=12.0,
            disk_percent=64.0,
            packet_loss_percent=0.0,
            cpu_iowait_percent=1.0,
        )
    )
    assert abs(phone_health.score - desktop_health.score) <= 5


async def test_the_summary_scores_a_phone_and_names_what_it_could_not_measure(
    client, phone
):
    resp = await client.get(
        f"/devices/{phone['device'].id}/summary", headers=_headers(phone["user"].id)
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["device"]["platform"] == "android"
    assert body["health"]["score"] is not None, "a phone must get a real score"
    assert body["health"]["band"] != "unknown"

    scored = {c["key"] for c in body["health"]["components"] if c["score"] is not None}
    assert scored == {"memory", "disk", "swap", "network"}
    # Named, so the UI can say which parts of the score were not measured
    # rather than leaving the reader to assume they were fine.
    assert set(body["health"]["unavailable"]) == {"cpu", "iowait"}


async def test_unmeasurable_metrics_are_null_and_never_zero(client, phone):
    body = (
        await client.get(
            f"/devices/{phone['device'].id}/summary", headers=_headers(phone["user"].id)
        )
    ).json()
    latest = body["latest"]

    assert latest is not None
    # The whole hard rule in one assertion block: none of these is 0.
    assert latest["cpu_percent"] is None
    assert latest["load1"] is None
    assert latest["process_count"] is None
    assert latest["disk_read_bytes_per_s"] is None
    assert latest["disk_write_bytes_per_s"] is None

    # And what it *can* measure is really there.
    assert latest["mem_percent"] == pytest.approx(55.0)
    assert latest["net_rx_bytes_per_s"] == pytest.approx(12_000.0)
    assert latest["packet_loss_percent"] == pytest.approx(0.0)
    assert latest["uptime_seconds"] == 86_400


async def test_battery_and_temperature_reach_the_summary(client, phone):
    """Phase 10b's one additive API change: three columns that were already
    stored end-to-end but had no way out of the database."""
    body = (
        await client.get(
            f"/devices/{phone['device'].id}/summary", headers=_headers(phone["user"].id)
        )
    ).json()
    latest = body["latest"]

    assert latest["battery_percent"] == pytest.approx(76.0)
    assert latest["battery_plugged"] is False
    assert latest["temperature_celsius"] == pytest.approx(31.4)


async def test_a_desktop_still_reports_null_for_the_new_fields(client, make_user):
    """The additive fields must not become a synthesised zero on a machine that
    has no battery — the same rule, applied to the change Phase 10b made."""
    user = await make_user("android-desktop@example.com")
    async with AdminSessionLocal() as session:
        device = await svc.register_device(session, user_id=user.id, name="mac")
        await write_samples(
            session,
            device_id=device.id,
            user_id=user.id,
            samples=[
                Sample(
                    ts=datetime.now(UTC) - timedelta(seconds=10),
                    resolution_seconds=10,
                    system=SystemSample(cpu_percent=10.0, mem_percent=30.0),
                )
            ],
        )
        await session.execute(
            sa.update(Device)
            .where(Device.id == device.id)
            .values(status="online", last_seen_at=sa.func.now())
        )
        await session.commit()

    body = (
        await client.get(
            f"/devices/{device.id}/summary", headers=_headers(user.id)
        )
    ).json()
    assert body["latest"]["battery_percent"] is None
    assert body["latest"]["temperature_celsius"] is None


async def test_the_disks_list_carries_the_phones_single_real_mount(client, phone):
    body = (
        await client.get(
            f"/devices/{phone['device'].id}/summary", headers=_headers(phone["user"].id)
        )
    ).json()

    assert [d["mount"] for d in body["disks"]] == ["/data"]
    # filesystem needs /proc/mounts, which is not readable. Null, not "ext4".
    assert body["disks"][0]["filesystem"] is None
    assert body["disks"][0]["percent"] == pytest.approx(64.0)


async def test_a_phone_enrols_through_the_same_endpoint_as_a_desktop(client, make_user):
    """`POST /enroll` already accepted platform="android" before Phase 10b, so
    the Kotlin enrollment path needed no backend change at all. This is the
    regression guard on that: the phone gets a real device row, marked android,
    and an opaque token it can connect with.
    """
    user = await make_user("android-enroller@example.com")
    async with AdminSessionLocal() as session:
        issued = await svc.create_enrollment_code(session, user_id=user.id)
        await session.commit()
        code = issued.code

    resp = await client.post(
        "/enroll",
        json={"code": code, "device_name": "Pixel 9", "platform": "android"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["agent_token"]
    # Opaque, and never the user's password or anything derived from it.
    assert "@" not in body["agent_token"]

    async with AdminSessionLocal() as session:
        device = await session.get(Device, uuid.UUID(body["device_id"]))
        assert device is not None
        assert device.platform == "android"
        assert device.name == "Pixel 9"
