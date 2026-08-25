"""Live-mode upshift/downshift: the LiveSupervisor reconciling an agent's push
cadence against app.live.registry's lease count, over a real WebSocket and a
real Redis. This is the concurrency-heavy part of Phase 3.

The viewer WebSocket doesn't exist yet (test_viewer_ws.py adds it), so these
tests act as the viewer would: claim/release a lease and publish the same
control-channel event app/live/viewer_ws.py will publish for real.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
import websockets

from app.live import bus, registry
from app.models import User
from app.services import enrollment_service as svc

HELLO = {
    "type": "hello",
    "protocol_version": 1,
    "host": {
        "hostname": "live-test-host",
        "os": "Darwin",
        "agent_version": "0.1.0",
    },
}


def _metrics(batch_id: str = "b1", cpu: float = 21.0) -> dict:
    return {
        "type": "metrics",
        "batch_id": batch_id,
        "samples": [
            {
                "ts": datetime.now(UTC).isoformat(),
                "resolution_seconds": 10,
                "system": {"cpu_percent": cpu},
                "disk_usage": [],
                "disk_io": [],
                "net": [],
                "latency": [],
                "processes": [],
            }
        ],
    }


@pytest.fixture
async def enrolled(admin_session):
    user = User(email="live-ws@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="live-mac")
    issued = await svc.issue_agent_token(admin_session, user_id=user.id, device_id=device.id)
    return {"user": user, "device": device, "token": issued.token}


def _connect(live_server: str, token: str):
    return websockets.connect(
        f"ws://{live_server}/ws/agent",
        additional_headers={"Authorization": f"Bearer {token}"},
        open_timeout=10,
    )


async def _send(ws, payload: dict) -> None:
    await ws.send(json.dumps(payload))


async def _recv(ws) -> dict:
    return json.loads(await ws.recv())


async def _recv_until(ws, frame_type: str, *, tries: int = 40) -> dict:
    """Drain frames (acks, pings) until one of `frame_type` shows up."""
    for _ in range(tries):
        frame = await asyncio.wait_for(_recv(ws), timeout=2)
        if frame["type"] == frame_type:
            return frame
    pytest.fail(f"never received a {frame_type!r} frame")


@pytest.fixture(autouse=True)
def _short_reconcile(monkeypatch):
    """Fast enough for a test, still exercises the real periodic tick."""
    import app.live.supervisor as supervisor_module

    monkeypatch.setattr(supervisor_module, "RECONCILE_INTERVAL_SECONDS", 0.2)


async def test_a_viewer_joining_upshifts_the_agent_to_live_mode(
    live_server, enrolled, redis_client
):
    user_id, device_id = enrolled["user"].id, enrolled["device"].id

    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        welcome = await _recv(ws)
        assert welcome["mode"] == "normal"

        await registry.claim(user_id, device_id, "viewer-1")
        await bus.publish_control(user_id, device_id, "viewer_joined")

        mode = await _recv_until(ws, "mode")
        assert mode["mode"] == "live"
        assert mode["push_interval_seconds"] == 1


async def test_a_viewer_leaving_downshifts_the_agent_to_normal_mode(
    live_server, enrolled, redis_client
):
    user_id, device_id = enrolled["user"].id, enrolled["device"].id

    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        await _recv(ws)

        await registry.claim(user_id, device_id, "viewer-1")
        await bus.publish_control(user_id, device_id, "viewer_joined")
        await _recv_until(ws, "mode")

        await registry.release(user_id, device_id, "viewer-1")
        await bus.publish_control(user_id, device_id, "viewer_left")

        mode = await _recv_until(ws, "mode")
        assert mode["mode"] == "normal"
        assert mode["push_interval_seconds"] == 10


async def test_the_agent_stays_live_until_every_viewer_has_left(
    live_server, enrolled, redis_client
):
    user_id, device_id = enrolled["user"].id, enrolled["device"].id

    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        await _recv(ws)

        await registry.claim(user_id, device_id, "viewer-1")
        await registry.claim(user_id, device_id, "viewer-2")
        await bus.publish_control(user_id, device_id, "viewer_joined")
        await _recv_until(ws, "mode")

        await registry.release(user_id, device_id, "viewer-1")
        await bus.publish_control(user_id, device_id, "viewer_left")

        # Still one lease remaining — no downshift should arrive. Give the
        # reconcile loop a couple of ticks to prove a negative.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(_recv_until(ws, "mode", tries=3), timeout=1.5)

        assert await registry.live_count(user_id, device_id) == 1


async def test_an_expired_lease_is_reconciled_without_a_leave_message(
    live_server, enrolled, redis_client, monkeypatch
):
    """A crashed viewer tab never sends 'viewer_left'. The periodic reconcile
    tick — not a control-channel message — is what must catch this."""
    import app.live.registry as registry_module

    user_id, device_id = enrolled["user"].id, enrolled["device"].id
    monkeypatch.setattr(registry_module, "LEASE_TTL_SECONDS", 0.3)

    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        await _recv(ws)

        await registry.claim(user_id, device_id, "viewer-1")
        await bus.publish_control(user_id, device_id, "viewer_joined")
        await _recv_until(ws, "mode")

        # No release(), no publish_control("viewer_left") — simulate the tab
        # just disappearing. The lease will expire on its own.
        mode = await _recv_until(ws, "mode", tries=60)
        assert mode["mode"] == "normal"


async def test_samples_are_published_only_while_a_viewer_is_registered(
    live_server, enrolled, redis_client
):
    user_id, device_id = enrolled["user"].id, enrolled["device"].id

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(bus.metrics_channel(user_id, device_id))
    await pubsub.get_message(timeout=1)

    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        await _recv(ws)

        # No viewer yet: a batch must not be published.
        await _send(ws, _metrics("no-viewer"))
        ack = await _recv_until(ws, "ack")
        assert ack["accepted"] == 1
        assert await pubsub.get_message(timeout=0.3) is None

        await registry.claim(user_id, device_id, "viewer-1")
        await bus.publish_control(user_id, device_id, "viewer_joined")
        await _recv_until(ws, "mode")

        await _send(ws, _metrics("with-viewer", cpu=77.0))
        await _recv_until(ws, "ack")

        for _ in range(20):
            msg = await pubsub.get_message(timeout=0.5)
            if msg and msg["type"] == "message":
                break
        else:
            pytest.fail("no published sample arrived")

        payload = json.loads(msg["data"])
        assert payload[0]["system"]["cpu_percent"] == 77.0

    await pubsub.unsubscribe()
    await pubsub.aclose()


async def test_a_lease_key_expires_even_if_nothing_ever_prunes_it(redis_client):
    """`live_count()` deletes the ZSET when its last member expires, but only
    an agent's own supervisor ever calls it. A viewer that opened Live
    Monitoring on a device whose agent never reconnected therefore left the
    key in Redis with nothing to clean up after it — small, but unbounded in
    the number of (user, device) pairs ever watched."""
    import uuid as uuid_module

    import app.live.registry as registry_module

    user_id, device_id = uuid_module.uuid4(), uuid_module.uuid4()
    await registry.claim(user_id, device_id, "viewer-1")

    key = registry_module._live_key(user_id, device_id)
    ttl = await redis_client.ttl(key)

    # -1 is "no expiry set", which is the state this guards against.
    assert 0 < ttl <= registry_module.LEASE_TTL_SECONDS

    # Renewing must push the expiry back out, not leave it decaying.
    await registry.claim(user_id, device_id, "viewer-1")
    assert await redis_client.ttl(key) >= ttl
    await redis_client.delete(key)
