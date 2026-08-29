"""Shared pieces of the three service installers.

Each platform's installer owns its own registration mechanism — a launchd
plist, a systemd unit, a Windows scheduled task — but they all have to answer
the same two questions the same way: *what command starts the agent*, and
*which config file does it read*. Getting either wrong produces a service that
launchd/systemd/Task Scheduler dutifully restarts forever while it fails.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from sentinel_agent.paths import Scope


class ServiceError(RuntimeError):
    """Installation failed for a reason the user can act on."""


def is_frozen() -> bool:
    """True when running from a PyInstaller build rather than a source venv."""
    return bool(getattr(sys, "frozen", False))


def agent_executable() -> list[str]:
    """The argv prefix that starts this agent, however it was installed.

    Three cases, in the order they are likely:

    * a PyInstaller build — `sys.executable` *is* the agent, and appending
      `-m sentinel_agent.cli` to it would be nonsense;
    * a venv install — the `sentinel-agent` console script sits beside the
      interpreter;
    * anything else (a source checkout run with `python -m`) — fall back to the
      interpreter plus the module.
    """
    if is_frozen():
        return [str(Path(sys.executable).resolve())]

    # Two directories, not one. On POSIX the console script sits beside the
    # interpreter in `bin/`; on Windows `python.exe` is at the venv root and
    # scripts go to `Scripts\` next to it, so looking only beside the
    # interpreter never finds it there. Adding the `.exe` name alone — which is
    # as far as the earlier fix went — was not enough, and the fallthrough to
    # the `-m` form is silent, so nothing said the console script had been
    # missed until a Windows runner asserted on it.
    interpreter_dir = Path(sys.executable).parent
    for directory in (interpreter_dir, interpreter_dir / "Scripts"):
        for name in ("sentinel-agent.exe", "sentinel-agent"):
            script = directory / name
            if script.exists():
                return [str(script)]
    return [sys.executable, "-m", "sentinel_agent.cli"]


def agent_command(
    config_path: Path, log_file: Path | None = None, *, insecure: bool = False
) -> list[str]:
    """The full argv a service manager should run.

    `--config` is a *global* option declared before the subparser, so it has to
    precede `run`. The reverse order parses as an unrecognised argument and the
    service manager just retries the failure until it gives up — which is a
    genuinely confusing symptom, so `tests/test_service_units.py` feeds this
    back through the real parser rather than asserting the strings are present.

    `log_file` is for the platforms whose service manager does not capture
    output. launchd redirects stdout in the plist and systemd owns the journal;
    Task Scheduler throws it away, so the Windows installer passes one.

    `insecure` renders `run --insecure`, and the installer passes it when the
    configured server is a remote `http://` one. Without that, `run`'s refusal
    to send the agent token in cleartext would turn into a service that fails
    at every restart forever — the operator made that choice at enrollment and
    should not have to discover it again from a service manager's logs. It is a
    *subcommand* option, so it goes after `run`, unlike `--config`.
    """
    argv = [*agent_executable(), "--config", str(config_path)]
    if log_file is not None:
        argv += ["--log-file", str(log_file)]
    argv = [*argv, "run"]
    if insecure:
        argv.append("--insecure")
    return argv


@dataclass(frozen=True, slots=True)
class InstallResult:
    """What was registered, and where, so the CLI can print something useful."""

    scope: Scope
    unit_path: Path
    logs: str


#: Directories a downloaded binary passes through but should not live in.
#: Compared against a forward-slash-normalised path, because Windows renders
#: these with backslashes and matching "/Downloads/" literally meant the
#: warning never fired there — on the one platform where a user is most likely
#: to run the binary straight out of Downloads, since SmartScreen already made
#: them go and find it.
TRANSIENT_DIRS = (
    "/Downloads/",
    "/Desktop/",
    "/tmp/",  # noqa: S108
    "/private/var/folders/",
    "/AppData/Local/Temp/",
)


def looks_transient(path: str) -> bool:
    """Pure, so the Windows answer is assertable from a Mac."""
    return any(part in path.replace("\\", "/") for part in TRANSIENT_DIRS)


def check_executable_location() -> str | None:
    """Warn when the binary being registered will not survive.

    A downloaded binary is very often still sitting in ~/Downloads when someone
    runs install-service, and a service pointed at a file the user later tidies
    away fails silently at the next boot. Returns a message, or None when the
    location looks durable.
    """
    if not is_frozen():
        return None

    resolved = Path(sys.executable).resolve()
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and str(resolved).startswith(str(Path(meipass).resolve())):
        # Should be unreachable: onefile sets sys.executable to the launcher,
        # not the unpacked temp dir. If it ever is reachable, registering it
        # would point a boot-time service at a directory that is deleted on
        # exit, so refuse rather than warn.
        raise ServiceError(
            "refusing to register a service pointing inside PyInstaller's "
            "temporary extraction directory"
        )

    if looks_transient(str(resolved)):
        return (
            f"the agent binary is at {resolved}, which looks like a temporary "
            f"location. Move it somewhere permanent and re-run install-service, "
            f"or the service will break the next time that folder is cleaned up."
        )
    return None
