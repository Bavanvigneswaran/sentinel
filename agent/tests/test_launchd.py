"""macOS service installation.

Only the plist is asserted here — actually bootstrapping into launchd is a
system side effect, exercised manually rather than in the test suite.
"""

import platform
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() != "Darwin", reason="launchd is macOS only"
)


def test_the_plist_runs_the_agent_with_its_config():
    from sentinel_agent.service import launchd

    plist = launchd.build_plist(Path("/tmp/agent.toml"))  # noqa: S108

    assert plist["Label"] == "com.sentinel.agent"
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True


def test_the_plist_arguments_actually_parse():
    """Asserting the arguments are merely *present* is not enough: --config is
    a global option declared before the subparser, so `run --config X` fails to
    parse and launchd just retries the failure until it gives up. Feed the real
    parser instead."""
    from sentinel_agent.cli import build_parser
    from sentinel_agent.service import launchd

    plist = launchd.build_plist(Path("/tmp/agent.toml"))  # noqa: S108
    argv = plist["ProgramArguments"]
    # Drop the interpreter/executable prefix; keep what argparse would see.
    args = build_parser().parse_args(argv[argv.index("--config") :])

    assert args.command == "run"
    assert args.config == "/tmp/agent.toml"  # noqa: S108


def test_the_plist_throttles_restarts():
    """KeepAlive without a throttle spins on a crash loop."""
    from sentinel_agent.service import launchd

    assert launchd.build_plist(Path("/tmp/a.toml"))["ThrottleInterval"] >= 10  # noqa: S108


def test_it_installs_as_a_user_agent_not_a_system_daemon():
    """The agent needs no elevated privileges — latency uses TCP connect rather
    than raw ICMP precisely so it does not — and a LaunchAgent needs no admin
    password."""
    from sentinel_agent.service import launchd

    assert "LaunchAgents" in str(launchd.PLIST_PATH)
    assert str(launchd.PLIST_PATH).startswith(str(Path.home()))


def test_logs_go_somewhere_findable():
    from sentinel_agent.service import launchd

    plist = launchd.build_plist(Path("/tmp/a.toml"))  # noqa: S108
    assert plist["StandardOutPath"].endswith("agent.log")
    assert plist["StandardErrorPath"].endswith("agent.err.log")
