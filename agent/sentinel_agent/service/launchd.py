"""macOS service installation via launchd.

This installs a per-user LaunchAgent, not a system-wide LaunchDaemon: the agent
needs no elevated privileges (latency uses TCP connect rather than raw ICMP
sockets precisely so it does not), and a user agent needs no admin password.

Phase 11 gave systemd and the Windows task an explicit `--scope system`, and
deliberately did *not* give one to launchd. A LaunchDaemon would have to be
written to `/Library/LaunchDaemons`, owned by root, and bootstrapped into the
`system` domain — root-requiring code that cannot be exercised from a test
suite or verified without breaking the developer's own machine. Writing it
blind and shipping it as though it worked is the one thing this project's
status notes are supposed to prevent, so `--scope system` on macOS is an
actionable error instead. See docs/PACKAGING.md.
"""

from __future__ import annotations

import os
import plistlib
import subprocess  # noqa: S404 — launchctl is the supported interface
from pathlib import Path

from sentinel_agent.paths import Scope, log_dir
from sentinel_agent.service.base import InstallResult, ServiceError, agent_command

LABEL = "com.sentinel.agent"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIR = log_dir("user", system="Darwin")


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """See systemd._run — launchctl is always present on a real macOS, but a
    status query should still never be the thing that crashes the CLI."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 127, "", f"{argv[0]}: {exc}")


def _reject_system_scope(scope: Scope) -> None:
    if scope == "system":
        raise ServiceError(
            "macOS system-scope installation (a LaunchDaemon) is not implemented. "
            "The agent needs no privileges, so a per-user LaunchAgent is the right "
            "install for a desktop; for an always-on Mac that must report before "
            "anyone logs in, write the LaunchDaemon by hand — docs/INSTALL.md has "
            "the plist."
        )


def build_plist(
    config_path: Path, scope: Scope = "user", *, insecure: bool = False
) -> dict:
    _reject_system_scope(scope)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "Label": LABEL,
        # --config is a global option declared before the subparser, so it has
        # to precede `run`. The reverse order parses as an unrecognised
        # argument and launchd just retries the failure until it gives up.
        # agent_command() is shared with systemd and the Windows task so all
        # three cannot drift on that ordering — or on finding a PyInstaller
        # binary rather than a venv console script.
        "ProgramArguments": agent_command(config_path, insecure=insecure),
        "RunAtLoad": True,
        # launchd restarts the agent if it exits for any reason. The agent has
        # its own reconnect backoff, so this only catches a hard crash.
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(LOG_DIR / "agent.log"),
        "StandardErrorPath": str(LOG_DIR / "agent.err.log"),
        "ProcessType": "Background",
        "LowPriorityIO": True,
    }


def install(
    config_path: Path, scope: Scope = "user", *, insecure: bool = False
) -> InstallResult:
    _reject_system_scope(scope)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)

    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(build_plist(config_path, scope, insecure=insecure), handle)

    uid = os.getuid()
    # bootout first so a reinstall picks up the new plist; it fails harmlessly
    # when nothing is loaded.
    _run(["/bin/launchctl", "bootout", f"gui/{uid}/{LABEL}"])
    result = _run(["/bin/launchctl", "bootstrap", f"gui/{uid}", str(PLIST_PATH)])
    if result.returncode != 0:
        raise ServiceError(f"launchctl bootstrap failed: {result.stderr.strip()}")

    return InstallResult(
        scope="user", unit_path=PLIST_PATH, logs=str(LOG_DIR / "agent.log")
    )


def uninstall(scope: Scope = "user") -> bool:
    _reject_system_scope(scope)
    uid = os.getuid()
    _run(["/bin/launchctl", "bootout", f"gui/{uid}/{LABEL}"])
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
        return True
    return False


def status(scope: Scope = "user") -> str:  # noqa: ARG001 — user scope is the only one
    uid = os.getuid()
    result = _run(["/bin/launchctl", "print", f"gui/{uid}/{LABEL}"])
    if result.returncode != 0:
        return "not installed"
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("state =", "pid =")):
            return stripped
    return "loaded"


__all__ = [
    "LABEL",
    "LOG_DIR",
    "PLIST_PATH",
    "build_plist",
    "install",
    "status",
    "uninstall",
]
