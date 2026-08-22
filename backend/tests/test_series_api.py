"""GET /devices/{id}/series — the time-range query API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db import AdminSessionLocal
from app.ingest.writer import write_samples
from app.models import User
from app.schemas.protocol import NetEntry, Sample, SystemSample
from app.security.tokens import issue_access_token
from app.services import enrollment_service as svc

SIGNUP = "/auth/signup"
OTHER = {"email": "series-stranger@example.com", "password": "a-perfectly-fine-password"}

BASE = (datetime.now(UTC) - timedelta(minutes=15)).replace(second=0, microsecond=0)


@pytest.fixture
async def device(admin_session):
    user = User(email="series-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    dev = await svc.register_device(admin_session, user_id=user.id, name="series-box")
    async with AdminSessionLocal() as session:
        await write_samples(
            session,
            device_id=dev.id,
            user_id=user.id,
            samples=[
                Sample(
                    ts=BASE + timedelta(seconds=offset),
                    resolution_seconds=10,
                    system=SystemSample(cpu_percent=cpu, mem_percent=50.0),
                    net=[NetEntry(nic="en0", rx_bytes_per_s=cpu * 100)],
                )
                # Offsets straddle three 1m buckets so a rolled-up request has
                # something to combine.
                for offset, cpu in ((10, 10.0), (20, 20.0), (30, 30.0),
                                    (70, 70.0), (130, 90.0))
            ],
        )
    return {"user": user, "device": dev}


def _headers(user_id) -> dict:
    token, _ = issue_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


async def test_requires_authentication(client, device):
    resp = await client.get(f"/devices/{device['device'].id}/series")
    assert resp.status_code == 401


async def test_another_users_device_is_404(client, device):
    token = (await client.post(SIGNUP, json=OTHER)).json()["access_token"]
    resp = await client.get(
        f"/devices/{device['device'].id}/series",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_a_missing_device_is_404(client, device):
    resp = await client.get(
        f"/devices/{uuid.uuid4()}/series", headers=_headers(device["user"].id)
    )
    assert resp.status_code == 404


async def test_a_short_window_returns_raw_points_in_order(client, device):
    resp = await client.get(
        f"/devices/{device['device'].id}/series?range_seconds=1800",
        headers=_headers(device["user"].id),
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["source"] == "raw"
    assert body["resolution_seconds"] == 10
    assert body["truncated"] is False
    timestamps = [p["ts"] for p in body["system"]]
    assert timestamps == sorted(timestamps)
    assert [p["cpu_percent"] for p in body["system"]] == [10.0, 20.0, 30.0, 70.0, 90.0]


async def test_the_response_states_which_source_and_bucket_it_used(client, device):
    """A chart drawn from two-minute averages has to be able to say so."""
    resp = await client.get(
        f"/devices/{device['device'].id}/series?range_seconds=86400",
        headers=_headers(device["user"].id),
    )
    body = resp.json()
    assert body["source"] == "1m"
    assert body["resolution_seconds"] == 120


async def test_only_the_requested_domains_are_queried(client, device):
    resp = await client.get(
        f"/devices/{device['device'].id}/series?range_seconds=1800&domains=net",
        headers=_headers(device["user"].id),
    )
    body = resp.json()
    assert body["system"] == []
    assert [p["nic"] for p in body["net"]] == ["en0"] * 5
    assert body["net"][0]["rx_bytes_per_s"] == 1000.0


async def test_several_domains_come_back_in_one_response(client, device):
    resp = await client.get(
        f"/devices/{device['device'].id}/series"
        "?range_seconds=1800&domains=system&domains=net",
        headers=_headers(device["user"].id),
    )
    body = resp.json()
    assert body["system"] and body["net"]


async def test_a_rolled_up_point_carries_its_own_sample_bookkeeping(client, device):
    """One sample and six samples are both "a bucket of CPU". sample_count is
    the only thing that distinguishes a quiet minute from a disconnected one."""
    resp = await client.get(
        f"/devices/{device['device'].id}/series?range_seconds=86400",
        headers=_headers(device["user"].id),
    )
    points = resp.json()["system"]
    assert points
    assert sum(p["sample_count"] for p in points) == 5
    assert all(p["sample_weight"] == p["sample_count"] * 10 for p in points)


async def test_an_unmeasured_metric_stays_null_through_the_api(client, device):
    resp = await client.get(
        f"/devices/{device['device'].id}/series?range_seconds=86400",
        headers=_headers(device["user"].id),
    )
    assert all(p["load1"] is None for p in resp.json()["system"])


async def test_an_unknown_domain_is_rejected_before_any_sql(client, device):
    resp = await client.get(
        f"/devices/{device['device'].id}/series?domains=metric_samples",
        headers=_headers(device["user"].id),
    )
    assert resp.status_code == 422


async def test_an_inverted_window_is_rejected(client, device):
    now = datetime.now(UTC)
    resp = await client.get(
        f"/devices/{device['device'].id}/series"
        f"?start={now.isoformat()}&end={(now - timedelta(hours=1)).isoformat()}",
        headers=_headers(device["user"].id),
    )
    assert resp.status_code == 422
