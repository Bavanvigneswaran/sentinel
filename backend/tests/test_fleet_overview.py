"""GET /fleet/overview and the per-device summary — the dashboard's payload."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.db import AdminSessionLocal
from app.ingest.writer import write_samples
from app.models import Device, User
from app.schemas.protocol import (
    DiskUsageEntry,
    LatencyEntry,
    NetEntry,
    Sample,
    SystemSample,
)
from app.security.tokens import issue_access_token
from app.services import enrollment_service as svc

SIGNUP = "/auth/signup"
OTHER = {"email": "fleet-stranger@example.com", "password": "a-perfectly-fine-password"}


def _headers(user_id) -> dict:
    token, _ = issue_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


async def _mark_online(device_id) -> None:
    async with AdminSessionLocal() as session:
        await session.execute(
            sa.update(Device)
            .where(Device.id == device_id)
            .values(status="online", last_seen_at=sa.func.now())
        )
        await session.commit()


@pytest.fixture
async def fleet(admin_session):
    """One online device with a full recent sample, one that never connected."""
    user = User(email="fleet-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()

    live = await svc.register_device(admin_session, user_id=user.id, name="live-box")
    idle = await svc.register_device(admin_session, user_id=user.id, name="never-connected")

    now = datetime.now(UTC)
    async with AdminSessionLocal() as session:
        await write_samples(
            session,
            device_id=live.id,
            user_id=user.id,
            samples=[
                Sample(
                    ts=now - timedelta(seconds=offset),
                    resolution_seconds=10,
                    system=SystemSample(
                        cpu_percent=20.0, mem_percent=55.0, swap_percent=0.0, load1=1.5
                    ),
                    disk_usage=[
                        DiskUsageEntry(mount="/", percent=12.0, used_bytes=100),
                        DiskUsageEntry(mount="/data", percent=91.0, used_bytes=900),
                    ],
                    net=[
                        NetEntry(nic="en0", rx_bytes_per_s=1000.0, tx_bytes_per_s=500.0),
                        NetEntry(nic="en1", rx_bytes_per_s=250.0, tx_bytes_per_s=125.0),
                    ],
                    latency=[
                        LatencyEntry(target="1.1.1.1", reachable=True,
                                     rtt_ms_avg=12.0, packet_loss_percent=0.0),
                        LatencyEntry(target="gateway", reachable=True,
                                     rtt_ms_avg=2.0, packet_loss_percent=4.0),
                    ],
                )
                for offset in (10, 20, 30)
            ],
        )
    await _mark_online(live.id)
    return {"user": user, "live": live, "idle": idle}


async def test_requires_authentication(client, fleet):
    assert (await client.get("/fleet/overview")).status_code == 401


async def test_another_user_sees_an_empty_fleet(client, fleet):
    token = (await client.post(SIGNUP, json=OTHER)).json()["access_token"]
    resp = await client.get(
        "/fleet/overview", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["devices"] == []
    assert resp.json()["totals"]["devices"] == 0


async def test_totals_count_both_status_and_health_band(client, fleet):
    body = (await client.get("/fleet/overview", headers=_headers(fleet["user"].id))).json()
    totals = body["totals"]
    assert totals["devices"] == 2
    assert totals["online"] == 1
    assert totals["pending"] == 1
    # The device that never connected has no score, so it lands in "unknown"
    # rather than dragging the fleet's health down with a zero.
    assert totals["unknown"] == 1
    assert totals["healthy"] + totals["degraded"] + totals["critical"] == 1


async def test_an_online_device_is_scored_from_its_recent_window(client, fleet):
    body = (await client.get("/fleet/overview", headers=_headers(fleet["user"].id))).json()
    live = next(d for d in body["devices"] if d["device"]["name"] == "live-box")

    assert live["health"]["score"] is not None
    assert live["health"]["band"] in {"healthy", "degraded", "critical"}
    scored = {c["key"] for c in live["health"]["components"] if c["score"] is not None}
    assert {"cpu", "memory", "disk", "network"} <= scored


async def test_a_device_that_never_connected_has_no_score_rather_than_zero(client, fleet):
    body = (await client.get("/fleet/overview", headers=_headers(fleet["user"].id))).json()
    idle = next(d for d in body["devices"] if d["device"]["name"] == "never-connected")

    assert idle["health"]["score"] is None
    assert idle["health"]["band"] == "unknown"
    assert idle["health"]["reason"]
    assert idle["latest"] is None
    assert idle["sparkline"]["ts"] == []


async def test_the_fullest_mount_comes_first_and_drives_the_disk_component(client, fleet):
    """A machine with a 91%-full data volume is not healthy because its root
    volume is at 12%. The worst mount is the one that will fail."""
    body = (await client.get("/fleet/overview", headers=_headers(fleet["user"].id))).json()
    live = next(d for d in body["devices"] if d["device"]["name"] == "live-box")

    assert [d["mount"] for d in live["disks"]] == ["/data", "/"]
    disk = next(c for c in live["health"]["components"] if c["key"] == "disk")
    assert disk["value"] == pytest.approx(91.0)


async def test_worst_packet_loss_wins_over_the_average(client, fleet):
    body = (await client.get("/fleet/overview", headers=_headers(fleet["user"].id))).json()
    live = next(d for d in body["devices"] if d["device"]["name"] == "live-box")

    network = next(c for c in live["health"]["components"] if c["key"] == "network")
    assert network["value"] == pytest.approx(4.0)
    assert live["latest"]["packet_loss_percent"] == pytest.approx(4.0)


async def test_network_and_disk_io_totals_are_summed_across_entities(client, fleet):
    body = (await client.get("/fleet/overview", headers=_headers(fleet["user"].id))).json()
    live = next(d for d in body["devices"] if d["device"]["name"] == "live-box")

    assert live["latest"]["net_rx_bytes_per_s"] == pytest.approx(1250.0)
    assert live["latest"]["net_tx_bytes_per_s"] == pytest.approx(625.0)
    # No disk IO was reported at all, so the total is unavailable, not zero.
    assert live["latest"]["disk_read_bytes_per_s"] is None


async def test_an_unmeasured_component_is_reported_as_unavailable(client, fleet):
    """This fixture never reports iowait — the same situation as a real macOS
    agent — and the response has to say so rather than scoring it."""
    body = (await client.get("/fleet/overview", headers=_headers(fleet["user"].id))).json()
    live = next(d for d in body["devices"] if d["device"]["name"] == "live-box")
    assert "iowait" in live["health"]["unavailable"]


async def test_the_sparkline_is_columnar_and_carries_its_resolution(client, fleet):
    body = (await client.get("/fleet/overview", headers=_headers(fleet["user"].id))).json()
    live = next(d for d in body["devices"] if d["device"]["name"] == "live-box")
    spark = live["sparkline"]

    assert spark["resolution_seconds"] == 60
    assert len(spark["ts"]) == len(spark["cpu_percent"]) == len(spark["mem_percent"])
    assert spark["ts"]


async def test_the_per_device_summary_matches_the_fleet_entry(client, fleet):
    headers = _headers(fleet["user"].id)
    overview = (await client.get("/fleet/overview", headers=headers)).json()
    from_fleet = next(d for d in overview["devices"] if d["device"]["name"] == "live-box")

    resp = await client.get(f"/devices/{fleet['live'].id}/summary", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["health"]["score"] == from_fleet["health"]["score"]
    assert resp.json()["disks"] == from_fleet["disks"]


async def test_another_users_device_summary_is_404(client, fleet):
    token = (await client.post(SIGNUP, json=OTHER)).json()["access_token"]
    resp = await client.get(
        f"/devices/{fleet['live'].id}/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_a_missing_device_is_404(client, fleet):
    resp = await client.get(
        f"/devices/{uuid.uuid4()}", headers=_headers(fleet["user"].id)
    )
    assert resp.status_code == 404
