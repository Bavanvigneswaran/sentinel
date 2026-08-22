"""Derived device status: DeviceOut downgrades a stale "online" row to
"offline" without trusting the stored column, since a killed backend process
never runs the ingest socket's disconnect handler."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.schemas.devices import DEVICE_STALE_AFTER_SECONDS, derive_device_status

SIGNUP = "/auth/signup"
CREDS = {"email": "status-owner@example.com", "password": "a-perfectly-fine-password"}


async def _auth_headers(client) -> dict:
    token = (await client.post(SIGNUP, json=CREDS)).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _set_device(admin_session, device_id, *, status: str, last_seen_at) -> None:
    await admin_session.execute(
        sa.text("UPDATE devices SET status = :s, last_seen_at = :t WHERE id = :i"),
        {"s": status, "t": last_seen_at, "i": device_id},
    )
    await admin_session.commit()


# --- the pure function ---------------------------------------------------------


def test_pending_is_never_touched():
    assert derive_device_status("pending", None) == "pending"


def test_offline_is_never_touched():
    assert derive_device_status("offline", datetime.now(UTC)) == "offline"


def test_online_with_a_null_last_seen_becomes_offline():
    assert derive_device_status("online", None) == "offline"


def test_online_with_a_fresh_last_seen_stays_online():
    fresh = datetime.now(UTC) - timedelta(seconds=1)
    assert derive_device_status("online", fresh) == "online"


def test_online_right_at_the_threshold_stays_online():
    edge = datetime.now(UTC) - timedelta(seconds=DEVICE_STALE_AFTER_SECONDS - 1)
    assert derive_device_status("online", edge) == "online"


def test_online_past_the_threshold_becomes_offline():
    stale = datetime.now(UTC) - timedelta(seconds=DEVICE_STALE_AFTER_SECONDS + 5)
    assert derive_device_status("online", stale) == "offline"


# --- through the API -------------------------------------------------------------


async def test_a_stale_online_device_is_reported_offline(client, admin_session):
    headers = await _auth_headers(client)
    created = (
        await client.post("/devices", json={"name": "crashed-box"}, headers=headers)
    ).json()

    stale = datetime.now(UTC) - timedelta(seconds=DEVICE_STALE_AFTER_SECONDS + 30)
    await _set_device(admin_session, created["id"], status="online", last_seen_at=stale)

    listed = (await client.get("/devices", headers=headers)).json()
    assert listed[0]["status"] == "offline"


async def test_a_fresh_online_device_is_reported_online(client, admin_session):
    headers = await _auth_headers(client)
    created = (
        await client.post("/devices", json={"name": "live-box"}, headers=headers)
    ).json()

    fresh = datetime.now(UTC) - timedelta(seconds=2)
    await _set_device(admin_session, created["id"], status="online", last_seen_at=fresh)

    listed = (await client.get("/devices", headers=headers)).json()
    assert listed[0]["status"] == "online"


async def test_a_never_connected_device_is_reported_pending(client):
    headers = await _auth_headers(client)
    created = (
        await client.post("/devices", json={"name": "fresh-box"}, headers=headers)
    ).json()
    assert created["status"] == "pending"

    listed = (await client.get("/devices", headers=headers)).json()
    assert listed[0]["status"] == "pending"
