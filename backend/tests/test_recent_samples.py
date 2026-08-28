"""GET /devices/{id}/samples/recent — the priming endpoint that fills a Live
Monitoring chart while the agent's live-mode upshift is still in flight."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.db import AdminSessionLocal
from app.ingest.writer import write_samples
from app.models import User
from app.schemas.protocol import (
    DiskUsageEntry,
    ProcessEntry,
    Sample,
    SystemSample,
)
from app.services import enrollment_service as svc

SIGNUP = "/auth/signup"
CREDS = {"email": "recent-owner@example.com", "password": "a-perfectly-fine-password"}
OTHER = {"email": "recent-stranger@example.com", "password": "a-perfectly-fine-password"}


async def _auth_headers(client, creds=CREDS) -> dict:
    token = (await client.post(SIGNUP, json=creds)).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _headers_for(user_id) -> dict:
    """The device fixture creates its own user directly in the DB (bypassing
    signup, like test_ingest_ws.py does), so authenticate it by minting an
    access token straight from the security layer rather than round-tripping
    through /auth/login with a password that was never set."""
    from app.security.tokens import issue_access_token

    token, _ = issue_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def device(admin_session):
    user = User(email="recent-agent@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    dev = await svc.register_device(admin_session, user_id=user.id, name="recent-mac")
    return {"user": user, "device": dev}


async def _write(device_id, user_id, samples: list[Sample]) -> None:
    async with AdminSessionLocal() as session:
        await write_samples(session, device_id=device_id, user_id=user_id, samples=samples)


def _sample(ts: datetime, cpu: float = 10.0) -> Sample:
    return Sample(
        ts=ts,
        resolution_seconds=10,
        system=SystemSample(cpu_percent=cpu, mem_percent=40.0),
        disk_usage=[DiskUsageEntry(mount="/", percent=55.0, used_bytes=100)],
        processes=[ProcessEntry(rank_by="cpu", rank=1, pid=1, name="init", cpu_percent=cpu)],
    )


async def test_requires_authentication(client, device):
    resp = await client.get(f"/devices/{device['device'].id}/samples/recent")
    assert resp.status_code == 401


async def test_a_missing_device_is_404(client):
    headers = await _auth_headers(client)
    import uuid

    resp = await client.get(f"/devices/{uuid.uuid4()}/samples/recent", headers=headers)
    assert resp.status_code == 404


async def test_another_users_device_is_404(client, device):
    headers = await _auth_headers(client, OTHER)
    resp = await client.get(f"/devices/{device['device'].id}/samples/recent", headers=headers)
    assert resp.status_code == 404


async def test_returns_recent_system_points_in_order(client, device):
    now = datetime.now(UTC)
    await _write(
        device["device"].id,
        device["user"].id,
        [
            _sample(now - timedelta(seconds=20), cpu=1.0),
            _sample(now - timedelta(seconds=10), cpu=2.0),
        ],
    )

    headers = await _headers_for(device["user"].id)

    resp = await client.get(
        f"/devices/{device['device'].id}/samples/recent?seconds=60", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [p["cpu_percent"] for p in body["system"]] == [1.0, 2.0]


async def test_returns_only_the_latest_disk_usage_and_process_snapshot(client, device):
    now = datetime.now(UTC)
    await _write(
        device["device"].id,
        device["user"].id,
        [
            _sample(now - timedelta(seconds=20), cpu=1.0),
            _sample(now - timedelta(seconds=5), cpu=9.0),
        ],
    )

    headers = await _headers_for(device["user"].id)

    resp = await client.get(
        f"/devices/{device['device'].id}/samples/recent?seconds=60", headers=headers
    )
    body = resp.json()
    assert len(body["disk_usage"]) == 1
    assert len(body["processes"]) == 1
    assert body["processes"][0]["cpu_percent"] == 9.0


async def test_samples_outside_the_window_are_excluded(client, device):
    now = datetime.now(UTC)
    await _write(device["device"].id, device["user"].id, [_sample(now - timedelta(seconds=500))])

    headers = await _headers_for(device["user"].id)

    resp = await client.get(
        f"/devices/{device['device'].id}/samples/recent?seconds=60", headers=headers
    )
    assert resp.json()["system"] == []


async def test_seconds_is_capped(client, device):
    headers = await _headers_for(device["user"].id)

    resp = await client.get(
        f"/devices/{device['device'].id}/samples/recent?seconds=99999", headers=headers
    )
    assert resp.status_code == 422


async def test_a_multi_entity_domain_keeps_the_newest_rows_not_the_oldest(client, device):
    """The primer feeds a chart that is about to continue in real time, so a
    truncated window must lose its *oldest* end, never its newest.

    Regression guard on a real defect: `_series` ordered ascending and
    LIMITed, and the row budget was one entity's worth. A live 1s session on
    an ordinary Mac (six NICs) produces 6 rows per second, so the default
    300s window is 1800 rows against a 900 cap — the endpoint returned the
    first half of the window and dropped everything the live socket was about
    to join onto, so the network chart opened ending minutes in the past.
    """
    from app.schemas.protocol import NetEntry

    now = datetime.now(UTC)
    nics = [f"en{i}" for i in range(6)]
    # 200 seconds x 6 NICs = 1200 net rows, comfortably over the old 900 cap
    # while staying well inside the 300s default window.
    samples = [
        Sample(
            ts=now - timedelta(seconds=offset),
            resolution_seconds=1,
            system=SystemSample(cpu_percent=1.0),
            net=[NetEntry(nic=nic, rx_bytes_per_s=float(offset)) for nic in nics],
        )
        for offset in range(200, 0, -1)
    ]
    await _write(device["device"].id, device["user"].id, samples)

    headers = await _headers_for(device["user"].id)
    resp = await client.get(
        f"/devices/{device['device'].id}/samples/recent?seconds=300", headers=headers
    )
    assert resp.status_code == 200

    net = resp.json()["net"]
    assert len(net) == 200 * len(nics)
    # Ascending by ts, and reaching all the way to the newest sample written.
    timestamps = [row["ts"] for row in net]
    assert timestamps == sorted(timestamps)
    newest = max(s.ts for s in samples)
    assert datetime.fromisoformat(timestamps[-1]) == newest
