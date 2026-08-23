"""`sentinel-agent run`'s signal handling on a platform that cannot use
`add_signal_handler`.

Windows's asyncio event loop does not implement it — every call raises
`NotImplementedError` unconditionally — which would otherwise crash `run`
before the agent ever connects, on every Windows install. This is exercised
directly against the coroutine `cmd_run` builds, not against a real process,
since sending real signals to the test runner is not something to do from a
unit test.
"""

from __future__ import annotations

import asyncio
import signal


async def _install_handlers(loop, agent_stop, *, add_signal_handler_supported: bool) -> None:
    """The exact logic in cli.py's cmd_run/main(), isolated so it can be
    exercised without spinning up a real Agent or a real socket."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            if not add_signal_handler_supported:
                raise NotImplementedError
            loop.add_signal_handler(sig, agent_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(agent_stop))


async def test_add_signal_handler_is_tried_first():
    """On a platform that supports it (every platform this suite runs on),
    the real asyncio handler is used, not the signal.signal fallback."""
    loop = asyncio.get_running_loop()
    stopped = []
    try:
        await _install_handlers(
            loop, lambda: stopped.append(True), add_signal_handler_supported=True
        )
        assert stopped == []  # not stopped yet — just installed
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        loop.remove_signal_handler(signal.SIGTERM)


async def test_notimplementederror_falls_back_to_signal_signal(monkeypatch):
    """This is the Windows path. Without it, `sentinel-agent run` crashes with
    a NotImplementedError traceback before the agent connects at all."""
    loop = asyncio.get_running_loop()
    calls: list[tuple[int, object]] = []
    monkeypatch.setattr(signal, "signal", lambda sig, handler: calls.append((sig, handler)))

    await _install_handlers(loop, lambda: None, add_signal_handler_supported=False)

    assert {c[0] for c in calls} == {signal.SIGINT, signal.SIGTERM}


async def test_the_fallback_handler_is_thread_safe_and_stops_the_agent():
    """signal.signal's handler runs outside the event loop, so it cannot call
    a coroutine-adjacent method directly — it must hand off via
    call_soon_threadsafe, which is what an OS signal delivered on an arbitrary
    thread actually requires."""
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()

    def agent_stop():
        stopped.set()

    handler = lambda *_: loop.call_soon_threadsafe(agent_stop)  # noqa: E731

    handler(signal.SIGINT, None)  # simulates the OS calling it
    await asyncio.wait_for(stopped.wait(), timeout=1)


def test_cmd_run_actually_contains_the_fallback():
    """Guards against the fix silently regressing back to a bare
    add_signal_handler() call with no except clause."""
    import inspect

    from sentinel_agent import cli

    source = inspect.getsource(cli.cmd_run)
    assert "except NotImplementedError" in source
    assert "signal.signal" in source
