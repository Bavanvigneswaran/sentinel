"""Unit tests for the live pipeline's Redis primitives: leases, tickets, and
pub/sub channel naming. No WebSocket involved — that's test_viewer_ws.py and
test_live_upshift.py.
"""

from __future__ import annotations

import uuid

import pytest

from app.live import bus, registry, tickets

# --- registry (lease ZSET) ---------------------------------------------------


async def test_a_fresh_device_has_no_live_viewers(redis_client):
    assert await registry.live_count(uuid.uuid4(), uuid.uuid4()) == 0


async def test_claiming_increments_the_live_count(redis_client):
    user_id, device_id = uuid.uuid4(), uuid.uuid4()
    await registry.claim(user_id, device_id, "viewer-1")
    assert await registry.live_count(user_id, device_id) == 1


async def test_two_distinct_viewers_both_count(redis_client):
    user_id, device_id = uuid.uuid4(), uuid.uuid4()
    await registry.claim(user_id, device_id, "viewer-1")
    await registry.claim(user_id, device_id, "viewer-2")
    assert await registry.live_count(user_id, device_id) == 2


async def test_claiming_twice_with_the_same_id_does_not_double_count(redis_client):
    user_id, device_id = uuid.uuid4(), uuid.uuid4()
    await registry.claim(user_id, device_id, "viewer-1")
    await registry.claim(user_id, device_id, "viewer-1")
    assert await registry.live_count(user_id, device_id) == 1


async def test_release_drops_the_count_immediately(redis_client):
    user_id, device_id = uuid.uuid4(), uuid.uuid4()
    await registry.claim(user_id, device_id, "viewer-1")
    await registry.release(user_id, device_id, "viewer-1")
    assert await registry.live_count(user_id, device_id) == 0


async def test_an_expired_lease_is_pruned_on_read(redis_client, monkeypatch):
    """A crashed viewer that never called release() must still fall out of the
    count — this is what makes the supervisor's periodic tick self-healing."""
    user_id, device_id = uuid.uuid4(), uuid.uuid4()

    import time as time_mod

    real_time = time_mod.time
    monkeypatch.setattr(registry.time, "time", lambda: real_time() - 3600)
    await registry.claim(user_id, device_id, "viewer-1")
    monkeypatch.setattr(registry.time, "time", real_time)

    assert await registry.live_count(user_id, device_id) == 0


async def test_leases_are_scoped_per_device(redis_client):
    user_id = uuid.uuid4()
    device_a, device_b = uuid.uuid4(), uuid.uuid4()
    await registry.claim(user_id, device_a, "viewer-1")
    assert await registry.live_count(user_id, device_b) == 0


# --- tickets ------------------------------------------------------------


async def test_a_minted_ticket_redeems_to_the_right_user(redis_client):
    user_id = uuid.uuid4()
    ticket = await tickets.mint_ticket(user_id)
    assert await tickets.redeem_ticket(ticket) == user_id


async def test_a_ticket_can_only_be_redeemed_once(redis_client):
    user_id = uuid.uuid4()
    ticket = await tickets.mint_ticket(user_id)
    await tickets.redeem_ticket(ticket)
    assert await tickets.redeem_ticket(ticket) is None


async def test_an_unknown_ticket_does_not_redeem(redis_client):
    assert await tickets.redeem_ticket("not-a-real-ticket") is None


async def test_two_tickets_for_the_same_user_are_independent(redis_client):
    user_id = uuid.uuid4()
    a = await tickets.mint_ticket(user_id)
    b = await tickets.mint_ticket(user_id)
    assert await tickets.redeem_ticket(a) == user_id
    assert await tickets.redeem_ticket(b) == user_id


# --- bus (pub/sub) --------------------------------------------------------


async def test_publish_samples_reaches_a_subscriber_on_the_tenant_channel(redis_client):
    from app.schemas.protocol import Sample, SystemSample

    user_id, device_id = uuid.uuid4(), uuid.uuid4()
    sample = Sample(
        ts="2026-01-01T00:00:00Z",
        resolution_seconds=1,
        system=SystemSample(cpu_percent=12.5),
    )

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(bus.metrics_channel(user_id, device_id))
    # Drain the subscribe-confirmation message before publishing.
    await pubsub.get_message(timeout=1)

    await bus.publish_samples(user_id, device_id, [sample])

    for _ in range(20):
        msg = await pubsub.get_message(timeout=0.5)
        if msg and msg["type"] == "message":
            break
    else:
        pytest.fail("no message received")

    import json

    payload = json.loads(msg["data"])
    assert len(payload) == 1
    assert payload[0]["system"]["cpu_percent"] == 12.5

    await pubsub.unsubscribe()
    await pubsub.aclose()


async def test_publish_samples_with_an_empty_list_publishes_nothing(redis_client):
    user_id, device_id = uuid.uuid4(), uuid.uuid4()
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(bus.metrics_channel(user_id, device_id))
    await pubsub.get_message(timeout=1)

    await bus.publish_samples(user_id, device_id, [])

    msg = await pubsub.get_message(timeout=0.3)
    assert msg is None

    await pubsub.unsubscribe()
    await pubsub.aclose()


async def test_publish_control_reaches_the_control_channel(redis_client):
    user_id, device_id = uuid.uuid4(), uuid.uuid4()
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(bus.control_channel(user_id, device_id))
    await pubsub.get_message(timeout=1)

    await bus.publish_control(user_id, device_id, "viewer_joined")

    for _ in range(20):
        msg = await pubsub.get_message(timeout=0.5)
        if msg and msg["type"] == "message":
            break
    else:
        pytest.fail("no message received")

    assert msg["data"] == "viewer_joined"

    await pubsub.unsubscribe()
    await pubsub.aclose()


async def test_channels_embed_user_id_before_device_id(redis_client):
    """The tenant-scoping property the whole design leans on."""
    user_id, device_id = uuid.uuid4(), uuid.uuid4()
    assert bus.metrics_channel(user_id, device_id) == f"sentinel:metrics:{user_id}:{device_id}"
    assert bus.control_channel(user_id, device_id) == f"sentinel:control:{user_id}:{device_id}"
