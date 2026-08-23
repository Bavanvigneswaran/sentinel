"""Where the agent's config, token and logs live on each platform.

Phase 2 resolved one directory from `Path.home()`, which is right for a
logged-in user running `sentinel-agent run` and wrong for every service account
Phase 11 introduces. A systemd system unit runs as root, and a Windows
scheduled task registered `/RU SYSTEM` has its profile under
`C:\\Windows\\System32\\config\\systemprofile` — neither is the HOME the person
who enrolled the agent was typing in.

So scope is explicit. `user` is the interactive/per-login install (the default,
and what `launchd.py` has always done); `system` is the boot-time,
machine-wide install. The installer writes the resolved path into the unit or
task it registers, so the running service never re-derives it from an
environment it does not have.

`SENTINEL_AGENT_HOME` still wins over everything — it is how the test agent and
a second enrollment on one machine stay separate.
"""

from __future__ import annotations

import os
import platform
import stat
import subprocess  # noqa: S404 — icacls is the only way to set a Windows ACL
from pathlib import Path
from typing import Literal

Scope = Literal["user", "system"]
SCOPES: tuple[Scope, ...] = ("user", "system")

CONFIG_FILENAME = "agent.toml"

#: Well-known SIDs, used instead of the names "SYSTEM" and "Administrators"
#: because those names are localised and `icacls` matches on the localised form.
_WIN_SID_SYSTEM = "*S-1-5-18"
_WIN_SID_ADMINISTRATORS = "*S-1-5-32-544"


def _system() -> str:
    return platform.system()


def config_dir(scope: Scope = "user", *, system: str | None = None) -> Path:
    """The directory holding `agent.toml` — which holds the agent token.

    Deliberately NOT XDG-aware on Linux/macOS: `~/.config/sentinel` is what
    Phase 2 shipped and what every existing install already uses, and honouring
    `XDG_CONFIG_HOME` now would silently relocate them.
    """
    system = system or _system()

    if system == "Windows":
        if scope == "system":
            # ProgramData, not Program Files: the agent rewrites this file on
            # re-enrolment. Its default ACL grants Users read, which is exactly
            # why secure_file() strips inheritance before the token is written.
            base = os.environ.get("ProgramData") or r"C:\ProgramData"
            return Path(base) / "Sentinel"
        # LOCALAPPDATA, not APPDATA: an agent token is scoped to one device, so
        # roaming it onto another machine would carry a credential that machine
        # has no business holding.
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Sentinel"

    if scope == "system":
        if system == "Darwin":
            return Path("/Library/Application Support/Sentinel")
        return Path("/etc/sentinel")

    return Path.home() / ".config" / "sentinel"


def config_path(scope: Scope = "user", *, system: str | None = None) -> Path:
    return config_dir(scope, system=system) / CONFIG_FILENAME


def default_config_dir() -> Path:
    """What an unqualified `sentinel-agent` command uses."""
    override = os.environ.get("SENTINEL_AGENT_HOME")
    return Path(override) if override else config_dir("user")


def log_dir(scope: Scope = "user", *, system: str | None = None) -> Path:
    """Where a *service* writes stdout/stderr.

    Only launchd and the Windows task need this. systemd goes to the journal,
    which is the platform's own answer and better than a file we rotate badly.
    """
    system = system or _system()

    if system == "Windows":
        return config_dir(scope, system=system) / "logs"
    if system == "Darwin":
        if scope == "system":
            return Path("/Library/Logs/Sentinel")
        return Path.home() / "Library" / "Logs" / "sentinel"
    if scope == "system":
        return Path("/var/log/sentinel")
    return Path.home() / ".local" / "state" / "sentinel"


# --- permissions ------------------------------------------------------------


class ConfigPermissionError(Exception):
    """Raised when the token file cannot be made private.

    Deliberately fatal rather than a warning: the caller's contract is that the
    token is never written to a location it could not lock down first.
    """


def windows_acl_argv(path: Path, *, username: str | None = None) -> list[str]:
    """The `icacls` invocation that makes `path` private.

    Pure, so it can be asserted on a Mac. `/inheritance:r` is the load-bearing
    part — without it the file keeps ProgramData's "Users: read" ACE and the
    token is readable by every account on the box. `/grant:r` replaces rather
    than adds, so re-running it cannot accumulate permissions.

    SYSTEM and Administrators are granted because they can take ownership of
    any file regardless; denying them would be theatre, and omitting SYSTEM
    would lock out the very service account a system-scope install runs as.
    """
    argv = [
        "icacls",
        str(path),
        "/inheritance:r",
        "/grant:r",
        f"{_WIN_SID_SYSTEM}:(F)",
        f"{_WIN_SID_ADMINISTRATORS}:(F)",
    ]
    if username is None:
        username = os.environ.get("USERNAME") or ""
    if username:
        argv.append(f"{username}:(R,W)")
    return argv


def open_private(path: Path, *, system: str | None = None) -> int:
    """Create/truncate `path`, make it private, and return a writable fd.

    Returns a *file descriptor* rather than a path so the caller writes to the
    exact file whose permissions were just verified. Reopening by name after
    locking it down would leave a window in which the entry could be replaced
    with a symlink, and the token would land on the symlink's target with the
    0600 check having passed against a file that no longer exists there.

    On Windows that window is unavoidable — `icacls` takes a path, not a
    handle, and `os.open()` has no meaningful mode argument — so the file is
    created empty, locked down, and only then reopened. The ordering still
    guarantees the important half: a failure to restrict never leaves a token
    on disk, because the token has not been written yet.
    """
    system = system or _system()
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC

    if system == "Windows":
        os.close(os.open(path, flags, 0o600))
        secure_file(path, system=system)
        return os.open(path, flags, 0o600)

    fd = os.open(path, flags, 0o600)
    try:
        # fchmod/fstat, not chmod/stat: same reason this returns an fd at all.
        os.fchmod(fd, 0o600)
        mode = stat.S_IMODE(os.fstat(fd).st_mode)
        if mode & 0o077:
            raise ConfigPermissionError(f"{path} is still group/world accessible ({oct(mode)})")
    except BaseException:
        os.close(fd)
        raise
    return fd


def secure_file(path: Path, *, system: str | None = None) -> None:
    """Make an existing `path` readable only by its owner.

    Prefer `open_private()` when creating the file — this is the by-path form,
    used on Windows (where there is no alternative) and for repairing a file
    that already exists.
    """
    system = system or _system()

    if system == "Windows":
        try:
            result = subprocess.run(  # noqa: S603
                windows_acl_argv(path), capture_output=True, text=True, check=False
            )
        except OSError as exc:
            # icacls missing or unrunnable. The caller's contract is that the
            # token is never written to a file we could not lock down, so this
            # has to surface as the same fatal error a non-zero exit does
            # rather than as a bare FileNotFoundError from the depths.
            raise ConfigPermissionError(
                f"could not run icacls to restrict {path}: {exc}"
            ) from exc
        if result.returncode != 0:
            raise ConfigPermissionError(
                f"could not restrict permissions on {path}: "
                f"{(result.stderr or result.stdout).strip()}"
            )
        return

    os.chmod(path, 0o600)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ConfigPermissionError(f"{path} is still group/world accessible ({oct(mode)})")


def is_private(path: Path, *, system: str | None = None) -> bool:
    """Best-effort 'is this file locked down?', for `sentinel-agent status`.

    On Windows this reports None-ish truth: reading an ACL properly needs
    pywin32, so we say nothing rather than guess. See docs/PACKAGING.md.
    """
    system = system or _system()
    if system == "Windows":
        return True
    return not (stat.S_IMODE(path.stat().st_mode) & 0o077)
