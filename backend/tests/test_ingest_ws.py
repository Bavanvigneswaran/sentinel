"""The agent ingest socket, over a real WebSocket against a real server."""

import json
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.db import AdminSessionLocal
from app.models import User
from app.services import enrollment_service as svc

HELLO = {
    "type": "hello",
    "protocol_version": 1,
    "host": {
        "hostname": "test-host",
        "os": "Darwin",
        "os_version": "25.5.0",
        "arch": "arm64",
        "cpu_cores": 10,
        "total_memory_bytes": 17179869184,
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
                "system": {"cpu_percent": cpu, "mem_percent": 44.0},
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
    user = User(email="ws@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="ws-mac")
    issued = await svc.issue_agent_token(admin_session, user_id=user.id, device_id=device.id)
    return {"user": user, "device": device, "token": issued.token, "token_id": issued.id}


def _connect(live_server: str, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return websockets.connect(
        f"ws://{live_server}/ws/agent", additional_headers=headers, open_timeout=10
    )


async def _send(ws, payload: dict) -> None:
    await ws.send(json.dumps(payload))


async def _recv(ws) -> dict:
    return json.loads(await ws.recv())


async def _device_row(device_id: uuid.UUID):
    async with AdminSessionLocal() as session:
        return (
            await session.execute(
                sa.text(
                    "SELECT hostname, os, arch, cpu_cores, agent_version, status, "
                    "enrolled_at, last_seen_at FROM devices WHERE id = :i"
                ),
                {"i": device_id},
            )
        ).one()


# --- authentication ---------------------------------------------------------


async def test_no_token_is_refused(live_server):
    with pytest.raises((InvalidStatus, ConnectionClosed, OSError)):
        async with _connect(live_server, None) as ws:
            await _recv(ws)


async def test_an_unknown_token_is_refused(live_server):
    with pytest.raises((InvalidStatus, ConnectionClosed, OSError)):
        async with _connect(live_server, "sag_definitely-not-a-real-token") as ws:
            await _recv(ws)


async def test_a_non_bearer_header_is_refused(live_server, enrolled):
    with pytest.raises((InvalidStatus, ConnectionClosed, OSError)):
        async with websockets.connect(
            f"ws://{live_server}/ws/agent",
            additional_headers={"Authorization": enrolled["token"]},
            open_timeout=10,
        ) as ws:
            await _recv(ws)


async def test_a_revoked_token_is_refused(live_server, enrolled, admin_session):
    await admin_session.execute(
        sa.text("UPDATE agent_tokens SET revoked_at = now() WHERE id = :i"),
        {"i": enrolled["token_id"]},
    )
    await admin_session.commit()

    with pytest.raises((InvalidStatus, ConnectionClosed, OSError)):
        async with _connect(live_server, enrolled["token"]) as ws:
            await _recv(ws)


async def test_a_soft_deleted_device_cannot_connect(live_server, enrolled, admin_session):
    await admin_session.execute(
        sa.text("UPDATE devices SET deleted_at = now() WHERE id = :i"),
        {"i": enrolled["device"].id},
    )
    await admin_session.commit()

    with pytest.raises((InvalidStatus, ConnectionClosed, OSError)):
        async with _connect(live_server, enrolled["token"]) as ws:
            await _recv(ws)


# --- handshake --------------------------------------------------------------


async def test_a_valid_token_completes_the_handshake(live_server, enrolled):
    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        welcome = await _recv(ws)

    assert welcome["type"] == "welcome"
    assert welcome["device_id"] == str(enrolled["device"].id)
    assert welcome["mode"] == "normal"
    assert welcome["push_interval_seconds"] == 10


async def test_the_handshake_records_host_metadata(live_server, enrolled):
    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        await _recv(ws)

    row = await _device_row(enrolled["device"].id)
    assert row.hostname == "test-host"
    assert row.os == "Darwin"
    assert row.arch == "arm64"
    assert row.cpu_cores == 10
    assert row.agent_version == "0.1.0"
    assert row.enrolled_at is not None


async def test_a_protocol_mismatch_is_not_retryable(live_server, enrolled):
    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, {**HELLO, "protocol_version": 999})
        frame = await _recv(ws)

    assert frame["type"] == "error"
    assert frame["code"] == "protocol_version"
    assert frame["retryable"] is False


# --- metrics ----------------------------------------------------------------


async def test_metrics_are_accepted_and_acked(live_server, enrolled):
    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        await _recv(ws)
        await _send(ws, _metrics("batch-42", cpu=63.5))
        ack = await _recv(ws)

    assert ack["type"] == "ack"
    assert ack["batch_id"] == "batch-42"
    assert ack["accepted"] == 1
    assert ack["rejected"] == 0

    async with AdminSessionLocal() as session:
        cpu = await session.scalar(sa.text("SELECT cpu_percent FROM metric_samples"))
    assert cpu == pytest.approx(63.5)


async def test_metrics_before_hello_are_refused(live_server, enrolled):
    """Writing samples for a device whose host we never confirmed is not a
    state worth supporting."""
    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, _metrics())
        frame = await _recv(ws)

    assert frame["type"] == "error"
    assert frame["code"] == "invalid_frame"
    assert frame["retryable"] is False


async def test_a_malformed_frame_is_rejected(live_server, enrolled):
    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        await _recv(ws)
        await _send(ws, {"type": "metrics", "batch_id": "x", "samples": "not-a-list"})
        frame = await _recv(ws)

    assert frame["type"] == "error"
    assert frame["code"] == "invalid_frame"


async def test_an_unknown_frame_type_is_rejected(live_server, enrolled):
    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        await _recv(ws)
        await _send(ws, {"type": "shutdown_everything"})
        frame = await _recv(ws)

    assert frame["type"] == "error"


async def test_an_oversized_frame_is_rejected_before_parsing(live_server, enrolled):
    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        await _recv(ws)
        await ws.send('{"type":"metrics","batch_id":"x","samples":[]}' + " " * (600 * 1024))
        frame = await _recv(ws)

    assert frame["type"] == "error"
    assert frame["code"] == "too_large"


async def test_out_of_range_values_are_rejected(live_server, enrolled):
    """cpu_percent is bounded 0-100 by the schema; 5000 is not a real reading."""
    bad = _metrics()
    bad["samples"][0]["system"]["cpu_percent"] = 5000

    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        await _recv(ws)
        await _send(ws, bad)
        frame = await _recv(ws)

    assert frame["type"] == "error"
    assert frame["code"] == "invalid_frame"


async def test_extra_fields_are_rejected(live_server, enrolled):
    """extra='forbid' — an agent sending fields we do not model is a version
    mismatch we want to hear about, not silently drop."""
    bad = _metrics()
    bad["samples"][0]["system"]["definitely_not_a_metric"] = 1

    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        await _recv(ws)
        await _send(ws, bad)
        frame = await _recv(ws)

    assert frame["type"] == "error"


async def test_stale_samples_are_counted_as_rejected(live_server, enrolled):
    from datetime import timedelta

    stale = _metrics()
    stale["samples"][0]["ts"] = (datetime.now(UTC) - timedelta(days=3)).isoformat()

    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        await _recv(ws)
        await _send(ws, stale)
        ack = await _recv(ws)

    assert ack["type"] == "ack"
    assert ack["accepted"] == 0
    assert ack["rejected"] == 1


async def test_the_device_goes_offline_on_disconnect(live_server, enrolled):
    async with _connect(live_server, enrolled["token"]) as ws:
        await _send(ws, HELLO)
        await _recv(ws)
        assert (await _device_row(enrolled["device"].id)).status == "online"

    # The server marks it offline in its disconnect handler.
    import asyncio

    for _ in range(40):
        if (await _device_row(enrolled["device"].id)).status == "offline":
            break
        await asyncio.sleep(0.05)

    assert (await _device_row(enrolled["device"].id)).status == "offline"
