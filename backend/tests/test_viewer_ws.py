"""The viewer WebSocket: ticket auth, subscribe/unsubscribe, tenant scoping,
sample delivery, and lease cleanup on disconnect."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.live import bus, registry
from app.live.tickets import mint_ticket
from app.models import User
from app.schemas.protocol import Sample, SystemSample
from app.services import enrollment_service as svc


@pytest.fixture
async def owner_and_device(admin_session):
    user = User(email="viewer-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="viewer-mac")
    return {"user": user, "device": device}


@pytest.fixture
async def other_users_device(admin_session):
    user = User(email="viewer-stranger@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="stranger-mac")
    return {"user": user, "device": device}


def _connect(live_server: str, ticket: str | None):
    qs = f"?ticket={ticket}" if ticket else ""
    return websockets.connect(f"ws://{live_server}/ws/viewer{qs}", open_timeout=10)


async def _send(ws, payload: dict) -> None:
    await ws.send(json.dumps(payload))


async def _recv(ws) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=5))


# --- authentication ----------------------------------------------------------


async def test_no_ticket_is_refused(live_server):
    with pytest.raises((InvalidStatus, ConnectionClosed, OSError)):
        async with _connect(live_server, None) as ws:
            await _recv(ws)


async def test_an_unknown_ticket_is_refused(live_server):
    with pytest.raises((InvalidStatus, ConnectionClosed, OSError)):
        async with _connect(live_server, "not-a-real-ticket") as ws:
            await _recv(ws)


async def test_a_ticket_can_only_be_used_once(live_server, owner_and_device, redis_client):
    ticket = await mint_ticket(owner_and_device["user"].id)

    async with _connect(live_server, ticket) as ws:
        await _send(ws, {"type": "subscribe", "device_id": str(owner_and_device["device"].id)})
        await _recv(ws)

    with pytest.raises((InvalidStatus, ConnectionClosed, OSError)):
        async with _connect(live_server, ticket) as ws:
            await _recv(ws)


# --- subscribe / unsubscribe --------------------------------------------------


async def test_subscribing_to_your_own_device_succeeds(
    live_server, owner_and_device, redis_client
):
    ticket = await mint_ticket(owner_and_device["user"].id)
    device_id = owner_and_device["device"].id

    async with _connect(live_server, ticket) as ws:
        await _send(ws, {"type": "subscribe", "device_id": str(device_id)})
        frame = await _recv(ws)

    assert frame["type"] == "subscribed"
    assert frame["device_id"] == str(device_id)
    assert frame["online"] is False  # never connected — status is "pending"


async def test_subscribing_to_someone_elses_device_is_not_found(
    live_server, owner_and_device, other_users_device, redis_client
):
    ticket = await mint_ticket(owner_and_device["user"].id)

    async with _connect(live_server, ticket) as ws:
        await _send(
            ws, {"type": "subscribe", "device_id": str(other_users_device["device"].id)}
        )
        frame = await _recv(ws)

    assert frame["type"] == "error"
    assert frame["code"] == "not_found"


async def test_subscribing_grants_the_agent_side_lease(
    live_server, owner_and_device, redis_client
):
    ticket = await mint_ticket(owner_and_device["user"].id)
    user_id, device_id = owner_and_device["user"].id, owner_and_device["device"].id

    async with _connect(live_server, ticket) as ws:
        await _send(ws, {"type": "subscribe", "device_id": str(device_id)})
        await _recv(ws)

        for _ in range(20):
            if await registry.live_count(user_id, device_id) == 1:
                break
            await asyncio.sleep(0.05)
        assert await registry.live_count(user_id, device_id) == 1

        await _send(ws, {"type": "unsubscribe", "device_id": str(device_id)})
        for _ in range(20):
            if await registry.live_count(user_id, device_id) == 0:
                break
            await asyncio.sleep(0.05)
        assert await registry.live_count(user_id, device_id) == 0


async def test_disconnecting_releases_every_lease(live_server, owner_and_device, redis_client):
    ticket = await mint_ticket(owner_and_device["user"].id)
    user_id, device_id = owner_and_device["user"].id, owner_and_device["device"].id

    async with _connect(live_server, ticket) as ws:
        await _send(ws, {"type": "subscribe", "device_id": str(device_id)})
        await _recv(ws)

    for _ in range(40):
        if await registry.live_count(user_id, device_id) == 0:
            break
        await asyncio.sleep(0.05)
    assert await registry.live_count(user_id, device_id) == 0


async def test_the_subscription_cap_is_enforced(
    live_server, owner_and_device, admin_session, redis_client
):
    user = owner_and_device["user"]
    device_ids = [owner_and_device["device"].id]
    for i in range(8):
        d = await svc.register_device(admin_session, user_id=user.id, name=f"extra-{i}")
        device_ids.append(d.id)
    assert len(device_ids) == 9  # one over MAX_SUBSCRIPTIONS_PER_SOCKET (8)

    ticket = await mint_ticket(user.id)
    async with _connect(live_server, ticket) as ws:
        for device_id in device_ids:
            await _send(ws, {"type": "subscribe", "device_id": str(device_id)})

        frames = [await _recv(ws) for _ in device_ids]

    subscribed = [f for f in frames if f["type"] == "subscribed"]
    rejected = [f for f in frames if f["type"] == "error"]
    assert len(subscribed) == 8
    assert len(rejected) == 1
    assert rejected[0]["code"] == "too_many_subscriptions"


# --- sample delivery -----------------------------------------------------------


async def test_published_samples_are_delivered_to_the_subscriber(
    live_server, owner_and_device, redis_client
):
    user_id, device_id = owner_and_device["user"].id, owner_and_device["device"].id
    ticket = await mint_ticket(user_id)

    async with _connect(live_server, ticket) as ws:
        await _send(ws, {"type": "subscribe", "device_id": str(device_id)})
        await _recv(ws)  # subscribed

        # Give the subscribe() call's pubsub.subscribe() a moment to land
        # before publishing, same as the bus-level fanout tests.
        await asyncio.sleep(0.2)

        sample = Sample(
            ts="2026-01-01T00:00:00Z",
            resolution_seconds=1,
            system=SystemSample(cpu_percent=55.5),
        )
        await bus.publish_samples(user_id, device_id, [sample])

        frame = await _recv(ws)

    assert frame["type"] == "samples"
    assert frame["device_id"] == str(device_id)
    assert frame["samples"][0]["system"]["cpu_percent"] == 55.5


async def test_samples_for_an_unsubscribed_device_are_not_delivered(
    live_server, owner_and_device, redis_client
):
    user_id, device_id = owner_and_device["user"].id, owner_and_device["device"].id
    ticket = await mint_ticket(user_id)

    async with _connect(live_server, ticket) as ws:
        # No subscribe at all.
        sample = Sample(
            ts="2026-01-01T00:00:00Z", resolution_seconds=1, system=SystemSample(cpu_percent=1.0)
        )
        await bus.publish_samples(user_id, device_id, [sample])

        with pytest.raises(asyncio.TimeoutError):
            await _recv(ws)


# --- malformed frames ----------------------------------------------------------


async def test_a_malformed_frame_closes_the_socket(live_server, owner_and_device, redis_client):
    ticket = await mint_ticket(owner_and_device["user"].id)

    async with _connect(live_server, ticket) as ws:
        await ws.send(json.dumps({"type": "subscribe", "device_id": "not-a-uuid"}))
        frame = await _recv(ws)
        assert frame["type"] == "error"
        assert frame["code"] == "invalid_frame"

        with pytest.raises(ConnectionClosed):
            await ws.recv()


async def test_an_oversized_frame_closes_the_socket(live_server, owner_and_device, redis_client):
    ticket = await mint_ticket(owner_and_device["user"].id)

    async with _connect(live_server, ticket) as ws:
        await ws.send(
            json.dumps({"type": "subscribe", "device_id": str(uuid.uuid4())}) + " " * 5000
        )
        frame = await _recv(ws)
        assert frame["type"] == "error"
        assert frame["code"] == "too_large"
