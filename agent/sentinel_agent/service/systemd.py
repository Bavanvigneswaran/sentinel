"""Linux service installation via systemd.

Two scopes, and the difference is not cosmetic:

* ``user`` — ``~/.config/systemd/user/sentinel-agent.service``, started by
  ``systemctl --user``. No root, matching launchd.py's per-user LaunchAgent
  choice. It only survives a reboot with logind lingering enabled, which the
  installer turns on and reports on rather than assuming.
* ``system`` — ``/etc/systemd/system/sentinel-agent.service``. Needs root to
  install, but it is the right answer for a headless server, which is most of
  what Linux agents run on and which has no "logged-in user" at all.

The unit is deliberately hardened. The agent needs no privileges — TCP-connect
latency probes instead of raw ICMP sockets was a Phase 2 decision made exactly
so this could be true — so a system unit runs with an empty capability bounding
set. That is what makes ``User=root`` here mean "owns its config file", not
"can do anything".
"""

from __future__ import annotations

import os
import shlex
import subprocess  # noqa: S404 — systemctl is the supported interface
from pathlib import Path

from sentinel_agent.paths import Scope
from sentinel_agent.service.base import (
    InstallResult,
    ServiceError,
    agent_command,
)

UNIT_NAME = "sentinel-agent.service"


def unit_path(scope: Scope = "user") -> Path:
    if scope == "system":
        return Path("/etc/systemd/system") / UNIT_NAME
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def _systemctl(scope: Scope) -> list[str]:
    return ["systemctl"] if scope == "system" else ["systemctl", "--user"]


def build_unit(config_path: Path, scope: Scope = "user") -> str:
    """Render the unit file. Pure — no filesystem, no systemd, so it is
    assertable from a Mac."""
    # shlex.quote each element rather than a bare join: systemd's ExecStart is
    # word-split, so a home directory with a space in it — which macOS and
    # Windows users create constantly and then carry to a Linux box — would
    # otherwise arrive as two arguments and fail to parse. systemd understands
    # POSIX single-quoting, which is what shlex.quote emits.
    exec_start = " ".join(shlex.quote(part) for part in agent_command(config_path))

    lines = [
        "[Unit]",
        "Description=Sentinel monitoring agent",
        "Documentation=https://github.com/sentinel/sentinel/blob/main/docs/INSTALL.md",
        # network-online rather than network.target: the agent's first act is an
        # outbound WSS connection, and network.target only means "the stack is
        # configured", not "a route exists".
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"ExecStart={exec_start}",
        # The agent has its own reconnect backoff, so a restart here only ever
        # catches a hard crash. RestartSec mirrors launchd's ThrottleInterval.
        "Restart=always",
        "RestartSec=10",
        "StandardOutput=journal",
        "StandardError=journal",
        "SyslogIdentifier=sentinel-agent",
        "",
        "# Hardening. The agent reads /proc and opens outbound TCP; it needs",
        "# nothing else, and nothing here is load-bearing for its function.",
        "NoNewPrivileges=yes",
        "PrivateTmp=yes",
        "ProtectSystem=strict",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectControlGroups=yes",
        "RestrictSUIDSGID=yes",
        "RestrictRealtime=yes",
        "LockPersonality=yes",
        # Empty bounding set: uid 0 without CAP_DAC_OVERRIDE cannot read files
        # its DAC permissions forbid, which is the whole point of running the
        # system unit as root without it actually being privileged.
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
        # AF_NETLINK is not optional: psutil's per-NIC counters go through
        # getifaddrs(), which is a netlink socket on Linux. Omitting it produces
        # an agent that connects fine and reports no network metrics at all.
        "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK",
        "SystemCallFilter=@system-service",
        "SystemCallErrorNumber=EPERM",
        # Deliberately NOT set: ProtectProc=. The top-processes collector reads
        # /proc/<pid>/ for processes it does not own, which is exactly what
        # ProtectProc=invisible hides. A metrics agent is the one service that
        # legitimately needs to see the process table.
        "",
        "[Install]",
    ]
    lines.append("WantedBy=multi-user.target" if scope == "system" else "WantedBy=default.target")
    lines.append("")

    if scope == "system":
        # ProtectHome only makes sense for the system unit; a user unit's
        # config lives under $HOME and it would hide it from itself.
        lines.insert(lines.index("PrivateTmp=yes") + 1, "ProtectHome=yes")

    return "\n".join(lines)


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a system tool, treating "it is not installed" as a failed run.

    Not every Linux has systemd (a container, WSL1, Alpine/OpenRC) and not
    every Windows exposes schtasks to the current user. Letting subprocess
    raise FileNotFoundError here would crash `sentinel-agent status` with a
    traceback — an OSError is not a ServiceError, so the CLI's handler does not
    catch it — when the honest answer is simply "not installed".
    """
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 127, "", f"{argv[0]}: {exc}")


def enable_lingering() -> tuple[bool, str]:
    """Make a user unit survive logout and reboot.

    Without lingering, `systemctl --user` services stop when the last session
    ends — so an agent installed this way monitors the machine only while
    someone is logged into it, which is not monitoring. Reported rather than
    silently attempted: it can legitimately fail (no polkit agent on a headless
    box) and the user needs to know their agent will not come back.
    """
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if not user:
        return False, "could not determine the current username"
    result = _run(["loginctl", "enable-linger", user])
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip() or "loginctl failed"
    return True, f"lingering enabled for {user}"


def install(config_path: Path, scope: Scope = "user") -> InstallResult:
    path = unit_path(scope)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_unit(config_path, scope))
    except OSError as exc:
        hint = " (run with sudo)" if scope == "system" else ""
        raise ServiceError(f"could not write {path}{hint}: {exc}") from exc

    systemctl = _systemctl(scope)
    for argv in ([*systemctl, "daemon-reload"], [*systemctl, "enable", "--now", UNIT_NAME]):
        result = _run(argv)
        if result.returncode != 0:
            raise ServiceError(
                f"{' '.join(argv)} failed: {(result.stderr or result.stdout).strip()}"
            )

    logs = f"journalctl {'--user ' if scope == 'user' else ''}-u {UNIT_NAME} -f"
    return InstallResult(scope=scope, unit_path=path, logs=logs)


def uninstall(scope: Scope = "user") -> bool:
    systemctl = _systemctl(scope)
    _run([*systemctl, "disable", "--now", UNIT_NAME])
    path = unit_path(scope)
    existed = path.exists()
    if existed:
        path.unlink()
    _run([*systemctl, "daemon-reload"])
    return existed


def status(scope: Scope = "user") -> str:
    result = _run([*_systemctl(scope), "is-active", UNIT_NAME])
    state = result.stdout.strip() or "unknown"
    return "not installed" if state == "inactive" and not unit_path(scope).exists() else state


__all__ = [
    "UNIT_NAME",
    "build_unit",
    "enable_lingering",
    "install",
    "status",
    "uninstall",
    "unit_path",
]
