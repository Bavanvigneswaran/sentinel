r"""Command-line entry point.

    sentinel-agent enroll --code X4T9-K2QM-7PDR
    sentinel-agent run
    sentinel-agent sample            # print one real sample and exit
    sentinel-agent install-service   # launchd / systemd / Task Scheduler

`--scope` (global, default "user") chooses *which* config file every command
reads, because that is also the question of where a service's token lives. A
system-scope install runs as root or LocalSystem and has no HOME to resolve
`~/.config/sentinel` from, so its config sits in `/etc/sentinel`,
`/Library/Application Support/Sentinel` or `%ProgramData%\Sentinel` instead.
Composing it across commands is deliberate:

    sudo sentinel-agent --scope system enroll --code X4T9-K2QM-7PDR
    sudo sentinel-agent --scope system install-service

`SENTINEL_AGENT_HOME` and an explicit `--config` both still win over `--scope`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import signal
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from sentinel_agent import __version__
from sentinel_agent.collectors import latency as latency_mod
from sentinel_agent.collectors.resources import collect_processes
from sentinel_agent.config import (
    DEFAULT_CONFIG_PATH,
    AgentConfig,
    ConfigError,
    requires_tls,
)
from sentinel_agent.enroll import EnrollmentError, enroll
from sentinel_agent.paths import SCOPES, Scope, config_path, is_private
from sentinel_agent.runner import LATENCY_INTERVAL_SECONDS, PROCESS_INTERVAL_SECONDS, Agent
from sentinel_agent.service import (
    ServiceError,
    check_executable_location,
    installer_for,
    is_frozen,
    mechanism,
    supported_scopes,
)

logger = logging.getLogger("sentinel_agent")


#: A monitoring agent runs forever, so an unrotated log file is itself an
#: operational problem. 5MB x 3 is a few days of reconnect chatter.
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3


def _configure_logging(verbose: bool, log_file: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if log_file:
        # Windows needs this. launchd redirects stdout to a file and systemd
        # sends it to the journal, but a Task Scheduler task's output is
        # discarded outright — so without a file handler the one platform where
        # the agent is hardest to debug is the only one with no log at all.
        path = Path(log_file).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(
                RotatingFileHandler(
                    path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
                )
            )
        except OSError as exc:
            # Never fatal: losing the log is bad, refusing to monitor because
            # of it is worse.
            print(f"Warning: could not open log file {path}: {exc}", file=sys.stderr)

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def _scope(args: argparse.Namespace) -> Scope:
    return getattr(args, "scope", "user") or "user"


def _config_path(args: argparse.Namespace) -> Path | None:
    """Which agent.toml this invocation is about.

    Precedence, most specific first: an explicit `--config`, then
    `SENTINEL_AGENT_HOME` (which is how the test agent stays separate from a
    real enrollment on the same machine), then the scope's platform default.
    Returning None lets AgentConfig.load() apply the last of those itself.
    """
    if args.config:
        return Path(args.config)
    if os.environ.get("SENTINEL_AGENT_HOME"):
        return None
    if _scope(args) == "system":
        return config_path("system")
    return None


def _load(args: argparse.Namespace) -> AgentConfig:
    config = AgentConfig.load(_config_path(args))
    if getattr(args, "server", None):
        config.server_url = args.server
    return config


# --- commands ---------------------------------------------------------------


def cmd_enroll(args: argparse.Namespace) -> int:
    config = _load(args)
    try:
        enroll(config, args.code, args.name, allow_insecure=args.insecure)
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
        # Before connecting, not on the first push. A cadence the server will
        # not accept otherwise surfaces as a fatal "invalid_frame" one push
        # interval into an otherwise healthy-looking session.
        config.validate_intervals()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if requires_tls(config.server_url) and not args.insecure:
        # A refusal, matching enrollment, with an explicit way past it.
        #
        # This used to warn and carry on, reasoning that killing a working
        # monitor over a transport choice its operator had already made was the
        # wrong trade. That reasoning had the exposure backwards. Enrollment
        # sends the credential once; `run` replays the same agent token in the
        # handshake on *every reconnect*, and the agent reconnects with backoff,
        # so an outage hands anyone on the path a fresh copy every few seconds —
        # along with every metric the machine reports. `run` is the
        # higher-exposure half of the pair, not the lower one.
        #
        # The escape hatch is the same one enrollment has, so an operator who
        # genuinely means it on a trusted LAN types it once in their service
        # definition rather than being stuck.
        print(
            f"Refusing to connect to {config.server_url} without TLS: the agent token "
            "is sent in cleartext on every reconnect, and so is everything this "
            "machine reports.\n"
            "Move the server behind https:// (see docs/PACKAGING.md on Tailscale "
            "Funnel), or pass --insecure if this is a network you trust.",
            file=sys.stderr,
        )
        return 1

    agent = Agent(config)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            # Graceful stop, so launchd's/systemd's/Task Scheduler's own signal
            # flushes rather than truncates. loop.add_signal_handler() is not
            # implemented on Windows's asyncio event loop — it raises
            # NotImplementedError unconditionally, which would otherwise crash
            # `run` before the agent connects at all, on every Windows install.
            # signal.signal() is the portable fallback; it cannot run agent.stop
            # (a coroutine-adjacent call unsafe from a signal handler) directly,
            # so it hands the loop a thread-safe callback instead.
            try:
                loop.add_signal_handler(sig, agent.stop)
            except NotImplementedError:
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(agent.stop))
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

    if args.timing:
        # What one sample actually costs on *this* machine, which is the only
        # place the question can be answered. The agent samples once a second;
        # a sample that takes longer than that cannot be taken once a second,
        # and the visible symptom is a Live Monitoring trace that crawls
        # rather than streams.
        #
        # Worth running on Windows specifically. On macOS two of the three
        # expensive probes are cheap only because the OS *denies* them —
        # net_connections() needs root here and fails in a tenth of a
        # millisecond, while on Windows it succeeds and walks the whole TCP
        # table. A cost this Mac cannot see is still a cost.
        async def timed() -> None:
            await agent.sample_once()  # prime the counters
            for i in range(1, 6):
                started = time.perf_counter()
                await agent.sample_once()
                elapsed = (time.perf_counter() - started) * 1000
                print(f"sample {i}: {elapsed:8.1f} ms", file=sys.stderr)
            budget = config.sample_interval_seconds * 1000
            print(
                f"\nbudget:   {budget:8.1f} ms (sample_interval_seconds"
                f" = {config.sample_interval_seconds})",
                file=sys.stderr,
            )

            # The five samples above all land within milliseconds of each
            # other, so PROCESS_INTERVAL_SECONDS/LATENCY_INTERVAL_SECONDS —
            # the very throttles that keep a running agent's per-sample cost
            # low — also keep them from ever re-firing inside this loop. A
            # machine where the process walk or a latency probe is what is
            # actually blowing the budget would print a clean report here
            # and nothing else. Timing them directly is what the runtime
            # warning's "run sample --timing" actually has to make good on.
            if config.collect_processes:
                started = time.perf_counter()
                await asyncio.to_thread(collect_processes)
                elapsed = (time.perf_counter() - started) * 1000
                print(
                    f"\nprocess list (every {PROCESS_INTERVAL_SECONDS}s):"
                    f" {elapsed:8.1f} ms",
                    file=sys.stderr,
                )
            targets = [latency_mod.LatencyTarget.parse(t) for t in config.latency_targets]
            if targets:
                started = time.perf_counter()
                await latency_mod.measure_all(targets)
                elapsed = (time.perf_counter() - started) * 1000
                print(
                    f"latency probe (every {LATENCY_INTERVAL_SECONDS}s):"
                    f" {elapsed:8.1f} ms",
                    file=sys.stderr,
                )

        asyncio.run(timed())
        return 0

    print(json.dumps(asyncio.run(once()), indent=2, default=str))
    return 0


def cmd_install_service(args: argparse.Namespace) -> int:
    system = platform.system()
    scope: Scope = _scope(args)

    try:
        installer = installer_for(system)
    except ServiceError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if scope not in supported_scopes(system):
        allowed = ", ".join(supported_scopes(system)) or "none"
        print(
            f"--scope {scope} is not supported on {system} (supported: {allowed}).",
            file=sys.stderr,
        )
        if system == "Darwin" and scope == "system":
            print("See docs/INSTALL.md for the hand-written LaunchDaemon.", file=sys.stderr)
        return 1

    config = _load(args)
    try:
        config.require_token()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        if scope == "system":
            # The likeliest mistake by a distance: enrolled as yourself, then
            # installed the service as root, which reads a different file.
            print(
                f"\nA system-scope service reads {config.path}, not your user "
                f"config. Enrol into it with:\n"
                f"  sudo sentinel-agent --scope system enroll --code <CODE>",
                file=sys.stderr,
            )
        return 1

    try:
        warning = check_executable_location()
    except ServiceError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if warning:
        print(f"Warning: {warning}\n", file=sys.stderr)

    # `run` refuses a remote http:// server unless told otherwise, so a service
    # registered without this would fail at every restart forever. The choice
    # was already made — and warned about — at enrollment; rediscovering it from
    # a service manager's logs is not a second decision worth forcing.
    insecure = requires_tls(config.server_url)
    if insecure:
        print(
            f"Warning: {config.server_url} is a remote http:// server, so the service "
            "is registered with --insecure. The agent token and every metric this "
            "machine reports travel in cleartext.\n",
            file=sys.stderr,
        )

    try:
        result = installer.install(config.path, scope, insecure=insecure)
    except ServiceError as exc:
        print(f"Could not install the service: {exc}", file=sys.stderr)
        return 1

    print(f"Installed {mechanism(system)} ({result.scope} scope) at {result.unit_path}")
    print(f"Config: {config.path}")
    print(f"Status: {installer.status(scope)}")
    print(f"Logs:   {result.logs}")

    if system == "Linux" and scope == "user":
        from sentinel_agent.service import systemd

        ok, detail = systemd.enable_lingering()
        if ok:
            print(f"Boot:   {detail}")
        else:
            # Not a failure of the install, but the user needs to know their
            # agent stops at logout unless they fix it.
            print(
                f"Boot:   NOT enabled — {detail}.\n"
                f"        A --user unit stops when your last session ends. Run\n"
                f"        `sudo loginctl enable-linger $USER`, or reinstall with "
                f"--scope system.",
                file=sys.stderr,
            )
    return 0


def cmd_uninstall_service(args: argparse.Namespace) -> int:
    try:
        installer = installer_for()
    except ServiceError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        removed = installer.uninstall(_scope(args))
    except ServiceError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Removed." if removed else "Was not installed.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = _load(args)
    exists = config.path.exists()

    print(f"Agent:        {__version__} ({'packaged build' if is_frozen() else 'source'})")
    print(f"Platform:     {platform.system()} {platform.machine()}")
    print(f"Scope:        {_scope(args)}")
    print(f"Config file:  {config.path}{'' if exists else '  (not created yet)'}")
    if exists and not is_private(config.path):
        # The token is in this file. Say so loudly rather than let a bad umask
        # or a hand-copied config quietly leave a credential readable.
        print("              WARNING: readable by other users on this machine")
    try:
        config.validate_intervals()
    except ConfigError as exc:
        # `status` describes a config rather than refusing one, so this is the
        # one place a bad cadence is reported without stopping anything.
        print(f"              WARNING: {exc}")
    print(f"Server:       {config.server_url}")
    print(f"Device ID:    {config.device_id or '(not enrolled)'}")
    print(f"Token:        {'present' if config.agent_token else 'absent'}")

    try:
        installer = installer_for()
    except ServiceError:
        print("Service:      no installer for this platform")
        return 0
    print(f"Service:      {installer.status(_scope(args))}  ({mechanism()})")
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
    parser.add_argument(
        "--log-file",
        help=(
            "also write logs here, rotated. Set by install-service on Windows, "
            "where Task Scheduler discards a task's output entirely."
        ),
    )
    parser.add_argument(
        "--scope",
        choices=SCOPES,
        default="user",
        help=(
            "which install this command is about: 'user' (per-login, no admin) "
            "or 'system' (boot-time, machine-wide). Chooses the default config "
            "location; ignored when --config or SENTINEL_AGENT_HOME is set."
        ),
    )

    sub = parser.add_subparsers(dest="command", required=True)

    enroll_parser = sub.add_parser("enroll", help="exchange a one-time code for a token")
    enroll_parser.add_argument("--code", required=True, help="enrollment code from the UI")
    enroll_parser.add_argument("--name", help="device name (default: this machine's hostname)")
    enroll_parser.add_argument(
        "--insecure",
        action="store_true",
        help=(
            "allow enrolling against a remote http:// server. The enrollment "
            "code and the long-lived agent token it returns both travel in "
            "cleartext; only do this on a network you trust."
        ),
    )
    enroll_parser.set_defaults(func=cmd_enroll)

    run_parser = sub.add_parser("run", help="run the agent in the foreground")
    run_parser.add_argument(
        "--insecure",
        action="store_true",
        help=(
            "allow connecting to a remote http:// server. The agent token is "
            "replayed in cleartext on every reconnect, and so is every metric "
            "this machine reports; only do this on a network you trust."
        ),
    )
    run_parser.set_defaults(func=cmd_run)
    sample_parser = sub.add_parser("sample", help="print one real sample and exit")
    sample_parser.add_argument(
        "--timing",
        action="store_true",
        help="time five samples instead of printing one, to see whether this "
        "machine can actually sample as often as it is configured to",
    )
    sample_parser.set_defaults(func=cmd_sample)
    sub.add_parser("status", help="show configuration and service state").set_defaults(
        func=cmd_status
    )
    sub.add_parser(
        "install-service", help="run at login/boot (launchd, systemd, or Task Scheduler)"
    ).set_defaults(func=cmd_install_service)
    sub.add_parser("uninstall-service", help="remove the installed service").set_defaults(
        func=cmd_uninstall_service
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose, args.log_file)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
