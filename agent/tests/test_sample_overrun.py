"""A sample that costs more than its own interval must say so.

This is the one failure mode of the agent that is invisible from every other
vantage point. The agent connects, handshakes and pushes exactly as a healthy
one does; the only difference is that its samples are stamped further apart
than the resolution they claim, and the only place that surfaces is a live
chart that advances in jumps — which reads as a slow network, or a server
problem, or a bug in the chart, and is none of those.

It is also platform-specific by construction: the two probes that cost the
most (`net_connections`, the process walk) are cheap on macOS precisely
*because* the OS denies them and they fail fast, and genuinely expensive on
Windows where they succeed. The machine that has the problem is therefore the
only one that can report it, which is what these warnings are for.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from sentinel_agent.config import AgentConfig
from sentinel_agent.runner import OVERRUN_WARNING_INTERVAL_SECONDS, Agent


@pytest.fixture
def agent(tmp_path) -> Agent:  # noqa: ANN001
    return Agent(
        AgentConfig(
            agent_token="sag_secret",  # noqa: S106
            path=tmp_path / "agent.toml",
            sample_interval_seconds=1,
        )
    )


def test_a_sample_inside_its_budget_says_nothing(agent: Agent, caplog) -> None:  # noqa: ANN001
    with caplog.at_level(logging.WARNING):
        agent._warn_if_overrunning(0.9)
    assert caplog.records == []


def test_an_overrunning_sample_warns_with_both_numbers(agent: Agent, caplog) -> None:  # noqa: ANN001
    with caplog.at_level(logging.WARNING):
        agent._warn_if_overrunning(2.5)

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    # The measured cost and the budget it missed, not just "slow": the whole
    # point is to be actionable on a machine nobody can attach a profiler to.
    assert "2500ms" in message
    assert "1000ms" in message
    assert "--timing" in message


def test_a_sustained_overrun_warns_once_a_minute_not_once_a_sample(
    agent: Agent,
    caplog,  # noqa: ANN001
    monkeypatch,  # noqa: ANN001
) -> None:
    clock = [1_000.0]
    monkeypatch.setattr("sentinel_agent.runner.time.monotonic", lambda: clock[0])

    with caplog.at_level(logging.WARNING):
        for _ in range(30):
            agent._warn_if_overrunning(2.5)
            clock[0] += 1
    # Thirty consecutive slow samples over thirty seconds: one line, not thirty.
    assert len(caplog.records) == 1

    with caplog.at_level(logging.WARNING):
        clock[0] += OVERRUN_WARNING_INTERVAL_SECONDS
        agent._warn_if_overrunning(2.5)
    assert len(caplog.records) == 2


def test_recovering_re_arms_the_warning(agent: Agent, caplog, monkeypatch) -> None:  # noqa: ANN001
    """A machine that goes slow, recovers, and goes slow again is two events.

    Without the reset the second episode would be silent for up to a minute —
    and a burst of slow samples that clears on its own is exactly the thing
    somebody watching a chart would want the log to corroborate.
    """
    clock = [1_000.0]
    monkeypatch.setattr("sentinel_agent.runner.time.monotonic", lambda: clock[0])

    with caplog.at_level(logging.WARNING):
        agent._warn_if_overrunning(2.5)
        clock[0] += 1
        agent._warn_if_overrunning(0.2)  # back inside budget
        clock[0] += 1
        agent._warn_if_overrunning(2.5)

    assert len(caplog.records) == 2


@pytest.mark.asyncio
async def test_a_slow_process_walk_does_not_delay_the_sample(
    agent: Agent, monkeypatch  # noqa: ANN001
) -> None:
    """The process refresh must not sit on the critical path of sample_once.

    On Windows, walking every process is the single most expensive collector
    (see PROCESS_INTERVAL_SECONDS's comment). Awaiting it inline used to stamp
    that delay onto the *whole* sample — CPU, memory, disk and net included —
    every time it ran, which is what a live chart freezing for a couple of
    seconds every ten seconds actually was. It must run concurrently instead,
    leaving only the process field briefly stale.
    """
    monkeypatch.setattr("sentinel_agent.runner.PROCESS_INTERVAL_SECONDS", 0)

    release = asyncio.Event()

    async def slow_collect_processes() -> list[dict]:
        await release.wait()
        return [{"pid": 1, "name": "slow"}]

    monkeypatch.setattr(
        "sentinel_agent.runner.collect_processes",
        lambda: pytest.fail("collect_processes must run via to_thread, not inline"),
    )
    monkeypatch.setattr(
        "sentinel_agent.runner.asyncio.to_thread",
        lambda fn: slow_collect_processes(),
    )

    loop = asyncio.get_event_loop()
    started = loop.time()
    sample = await asyncio.wait_for(agent.sample_once(), timeout=1.0)
    elapsed = loop.time() - started

    assert elapsed < 0.5
    assert sample["processes"] == []  # stale (empty) value, not blocked-for

    release.set()
    await agent._process_task
    assert agent._latest_processes == [{"pid": 1, "name": "slow"}]
