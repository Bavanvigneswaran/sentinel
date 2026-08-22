"""macOS service installation via launchd.

Only launchd is implemented here. systemd and Windows Service support lands in
Phase 11 alongside the packaged builds, where they can be installed and
verified on real targets rather than written blind.

This installs a per-user LaunchAgent, not a system-wide LaunchDaemon: the agent
needs no elevated privileges (latency uses TCP connect rather than raw ICMP
sockets precisely so it does not), and a user agent needs no admin password.
"""

from __future__ import annotations

import os
import plistlib
import subprocess  # noqa: S404 — launchctl is the supported interface
import sys
from pathlib import Path

LABEL = "com.sentinel.agent"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIR = Path.home() / "Library" / "Logs" / "sentinel"


def _executable() -> list[str]:
    """Prefer the installed console script; fall back to `python -m`."""
    script = Path(sys.executable).parent / "sentinel-agent"
    if script.exists():
        return [str(script)]
    return [sys.executable, "-m", "sentinel_agent.cli"]


def build_plist(config_path: Path) -> dict:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "Label": LABEL,
        "ProgramArguments": [*_executable(), "run", "--config", str(config_path)],
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


def install(config_path: Path) -> Path:
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)

    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(build_plist(config_path), handle)

    uid = os.getuid()
    # bootout first so a reinstall picks up the new plist; it fails harmlessly
    # when nothing is loaded.
    subprocess.run(  # noqa: S603
        ["/bin/launchctl", "bootout", f"gui/{uid}/{LABEL}"],
        capture_output=True, check=False,
    )
    result = subprocess.run(  # noqa: S603
        ["/bin/launchctl", "bootstrap", f"gui/{uid}", str(PLIST_PATH)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {result.stderr.strip()}")

    return PLIST_PATH


def uninstall() -> bool:
    uid = os.getuid()
    subprocess.run(  # noqa: S603
        ["/bin/launchctl", "bootout", f"gui/{uid}/{LABEL}"],
        capture_output=True, check=False,
    )
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
        return True
    return False


def status() -> str:
    uid = os.getuid()
    result = subprocess.run(  # noqa: S603
        ["/bin/launchctl", "print", f"gui/{uid}/{LABEL}"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return "not installed"
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("state =", "pid =")):
            return stripped
    return "loaded"
