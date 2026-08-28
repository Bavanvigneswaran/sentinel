"""The push loop must react to a `mode` frame that arrives mid-interval.

Opening Live Monitoring sends the agent a `mode` frame within a round trip,
but the agent spends almost all of its time asleep between pushes. If that
sleep is blind, the frame sits unread until the *next* push cycle: the trace
stays still for up to a full push interval, and the batch that finally goes
out was built in the old mode — one 10s-collapsed row rather than the 1s
points a viewer is waiting for.

That is the same shape as the Kotlin collector's Phase 10b bug ("changing an
interval does not shorten a sleep that has already started"), reached from the
pusher's side instead of the sampler's. These tests pin the fix: the wait
reads the socket, and a cadence change ends it early.
"""

from __future__ import annotations

import asyncio

import pytest

from sentinel_agent.transport.client import AgentConnection, FatalTransportError


class FakeWebSocket:
    """A socket that yields queued frames, then blocks forever like a real
    idle connection does — so a test that expects `idle()` to *wait* actually
    waits, rather than racing an exhausted iterator."""

    def __init__(self, frames: list[dict]) -> None:
        self._frames = list(frames)
        self.sent: list[dict] = []

    async def recv(self) -> str:
        import json

        if self._frames:
            return json.dumps(self._frames.pop(0))
        await asyncio.Event().wait()  # never resolves
        raise AssertionError("unreachable")

    async def send(self, payload: str) -> None:
        import json

        self.sent.append(json.loads(payload))


def _connection(frames: list[dict]) -> tuple[AgentConnection, FakeWebSocket]:
    ws = FakeWebSocket(frames)
    return AgentConnection(ws), ws


@pytest.mark.asyncio
async def test_a_cadence_change_ends_the_idle_wait_immediately() -> None:
    conn, _ = _connection([{"type": "mode", "mode": "live", "push_interval_seconds": 1}])

    # A ten second wait that must not take ten seconds.
    await asyncio.wait_for(conn.idle(10), timeout=1)

    assert conn.mode == "live"
    assert conn.push_interval_seconds == 1


@pytest.mark.asyncio
async def test_a_mode_frame_that_changes_nothing_does_not_end_the_wait() -> None:
    """Returning early for every mode frame would rebuild the same batch a
    moment early for no reason — only an actual cadence change is worth it."""
    conn, _ = _connection(
        [{"type": "mode", "mode": "normal", "push_interval_seconds": 10}]
    )
    conn.push_interval_seconds = 10

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(conn.idle(10), timeout=0.2)


@pytest.mark.asyncio
async def test_the_wait_runs_out_on_its_own_when_the_server_is_quiet() -> None:
    conn, _ = _connection([])

    # Returns by deadline, not by frame — the ordinary case.
    await asyncio.wait_for(conn.idle(0.1), timeout=1)


@pytest.mark.asyncio
async def test_a_ping_is_answered_without_ending_the_wait() -> None:
    conn, ws = _connection([{"type": "ping"}])

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(conn.idle(10), timeout=0.2)

    assert {"type": "pong"} in ws.sent


@pytest.mark.asyncio
async def test_a_fatal_error_arriving_while_idle_is_raised_not_swallowed() -> None:
    """A revoked token can arrive between pushes as easily as during one.
    Swallowing it here would leave the agent idling against a socket the
    server has already given up on — which is precisely how a revoked device
    went on 'reporting' before Phase 13 closed the ingest-side half of this."""
    conn, _ = _connection(
        [{"type": "error", "code": "unauthorized", "message": "revoked", "retryable": False}]
    )

    with pytest.raises(FatalTransportError):
        await asyncio.wait_for(conn.idle(10), timeout=1)
