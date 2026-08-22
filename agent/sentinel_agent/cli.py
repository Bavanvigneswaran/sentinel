"""Command-line entry point.

    sentinel-agent enroll --code X4T9-K2QM-7PDR
    sentinel-agent run
    sentinel-agent sample            # print one real sample and exit
    sentinel-agent install-service   # macOS launchd (Phase 11 adds the rest)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import platform
import signal
import sys
from pathlib import Path

from sentinel_agent import __version__
from sentinel_agent.config import DEFAULT_CONFIG_PATH, AgentConfig, ConfigError
from sentinel_agent.enroll import EnrollmentError, enroll
from sentinel_agent.runner import Agent

logger = logging.getLogger("sentinel_agent")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _load(args: argparse.Namespace) -> AgentConfig:
    config = AgentConfig.load(Path(args.config) if args.config else None)
    if getattr(args, "server", None):
        config.server_url = args.server
    return config


# --- commands ---------------------------------------------------------------


def cmd_enroll(args: argparse.Namespace) -> int:
    config = _load(args)
    try:
        enroll(config, args.code, args.name)
    except EnrollmentError as exc:
        print(f"Enrollment failed: {exc}", file=sys.stderr)
        return 1
    print(f"Enrolled as device {config.device_id}")
    print(f"Configuration written to {config.path}")
    print("\nStart the agent with:  sentinel-agent run")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = _load(args)
    try:
        config.require_token()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    agent = Agent(config)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            # Graceful stop, so launchd's SIGTERM flushes rather than truncates.
            loop.add_signal_handler(sig, agent.stop)
        await agent.run()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    """Print one real sample. The fastest way to see what this machine exposes
    and what it does not."""
    config = _load(args)
    agent = Agent(config)

    async def once() -> dict:
        # The first reading of any counter has no interval to differentiate
        # against, so take two and show the second.
        await agent.sample_once()
        await asyncio.sleep(config.sample_interval_seconds)
        return await agent.sample_once()

    print(json.dumps(asyncio.run(once()), indent=2, default=str))
    return 0


def cmd_install_service(args: argparse.Namespace) -> int:
    system = platform.system()
    if system != "Darwin":
        print(
            f"Service installation is implemented for macOS only right now; "
            f"this is {system}. Run `sentinel-agent run` directly, or wait for "
            f"Phase 11 which adds systemd and Windows Service support.",
            file=sys.stderr,
        )
        return 1

    from sentinel_agent.service import launchd

    config = _load(args)
    try:
        config.require_token()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        path = launchd.install(config.path)
    except RuntimeError as exc:
        print(f"Could not install the service: {exc}", file=sys.stderr)
        return 1

    print(f"Installed LaunchAgent at {path}")
    print(f"Status: {launchd.status()}")
    print("Logs:   ~/Library/Logs/sentinel/agent.log")
    return 0


def cmd_uninstall_service(args: argparse.Namespace) -> int:
    if platform.system() != "Darwin":
        print("Nothing to uninstall on this platform.", file=sys.stderr)
        return 1
    from sentinel_agent.service import launchd

    print("Removed." if launchd.uninstall() else "Was not installed.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = _load(args)
    print(f"Config file:  {config.path}{'' if config.path.exists() else '  (not created yet)'}")
    print(f"Server:       {config.server_url}")
    print(f"Device ID:    {config.device_id or '(not enrolled)'}")
    print(f"Token:        {'present' if config.agent_token else 'absent'}")
    if platform.system() == "Darwin":
        from sentinel_agent.service import launchd

        print(f"Service:      {launchd.status()}")
    return 0


# --- parser -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel-agent", description="Sentinel monitoring agent"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--config", help=f"path to agent.toml (default: {DEFAULT_CONFIG_PATH})"
    )
    parser.add_argument("--server", help="override the server URL")

    sub = parser.add_subparsers(dest="command", required=True)

    enroll_parser = sub.add_parser("enroll", help="exchange a one-time code for a token")
    enroll_parser.add_argument("--code", required=True, help="enrollment code from the UI")
    enroll_parser.add_argument("--name", help="device name (default: this machine's hostname)")
    enroll_parser.set_defaults(func=cmd_enroll)

    sub.add_parser("run", help="run the agent in the foreground").set_defaults(func=cmd_run)
    sub.add_parser("sample", help="print one real sample and exit").set_defaults(func=cmd_sample)
    sub.add_parser("status", help="show configuration and service state").set_defaults(
        func=cmd_status
    )
    sub.add_parser("install-service", help="install as a macOS LaunchAgent").set_defaults(
        func=cmd_install_service
    )
    sub.add_parser("uninstall-service", help="remove the macOS LaunchAgent").set_defaults(
        func=cmd_uninstall_service
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
