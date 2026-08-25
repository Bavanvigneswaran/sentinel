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
import time
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


def current_user_sid(*, run=subprocess.run) -> str | None:  # noqa: ANN001
    """The SID of the account this process is running as, or None.

    `whoami /user /fo csv /nh` prints `"HOST\\name","S-1-5-21-..."`, and is
    present on every Windows since Vista. A SID is used in preference to
    `%USERNAME%` for exactly the reason the SYSTEM/Administrators constants
    above are: it is the identifier icacls resolves unambiguously, with no
    localisation and no dependence on an environment variable that may not be
    set in the process actually doing the work.
    """
    try:
        result = run(
            ["whoami", "/user", "/fo", "csv", "/nh"],  # noqa: S607 — a Windows builtin
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for field in (result.stdout or "").replace('"', "").split(","):
        candidate = field.strip()
        if candidate.startswith("S-1-"):
            return candidate
    return None


def windows_principal(*, sid_lookup=current_user_sid) -> str | None:  # noqa: ANN001
    """Who to grant the token file to, most reliable form first.

    Falls back to `DOMAIN\\USERNAME` and then bare `USERNAME`, because a SID
    lookup failing is not by itself a reason to refuse an install that would
    otherwise work. Returns None only when nothing at all identifies the
    caller — and the caller's job is then to refuse, never to carry on.
    """
    sid = sid_lookup()
    if sid:
        return f"*{sid}"
    username = os.environ.get("USERNAME") or ""
    if not username:
        return None
    domain = os.environ.get("USERDOMAIN") or ""
    return f"{domain}\\{username}" if domain else username


def windows_acl_argv(path: Path, *, principal: str) -> list[str]:
    """The `icacls` invocation that makes `path` private.

    Pure, so it can be asserted on a Mac. `/inheritance:r` is the load-bearing
    part — without it the file keeps ProgramData's "Users: read" ACE and the
    token is readable by every account on the box. `/grant:r` replaces rather
    than adds, so re-running it cannot accumulate permissions.

    SYSTEM and Administrators are granted because they can take ownership of
    any file regardless; denying them would be theatre, and omitting SYSTEM
    would lock out the very service account a system-scope install runs as.

    `principal` is REQUIRED, and that is the whole point. This function used to
    read `%USERNAME%` itself and silently emit no user grant at all when it was
    empty — while `/inheritance:r` had already stripped every inherited
    permission. The result was a token file granted to SYSTEM and
    Administrators and to nobody else: icacls exits 0, so nothing raises, and
    the person who just enrolled cannot read, rewrite, or even delete their own
    credential. Observed on a real Windows machine, where the only way to clear
    it was an elevated shell — Administrators being, by construction, the one
    principal that was still on the ACL.

    The owner gets (F), not (R,W): they own the file, `save()` rewrites it on
    every re-enrolment, and (R,W) does not include DELETE, so the previous
    grant could not have been cleaned up by its owner even when it did apply.
    Privacy here comes from `/inheritance:r` plus a three-entry ACL, not from
    withholding rights from the account the file exists to serve.
    """
    return [
        "icacls",
        str(path),
        "/inheritance:r",
        "/grant:r",
        f"{_WIN_SID_SYSTEM}:(F)",
        f"{_WIN_SID_ADMINISTRATORS}:(F)",
        f"{principal}:(F)",
    ]


#: How long a freshly ACL'd file may transiently deny access before this gives
#: up and lets the real PermissionError surface.
_REOPEN_RETRY_SECONDS = (0.05, 0.1, 0.2, 0.4, 0.8)


def _reopen_after_acl_change(path: Path, flags: int) -> int:
    """Reopen `path` right after `icacls` has just rewritten its ACL,
    tolerating the access being transiently denied.

    **This did not fix the bug it was written for, and the theory behind it was
    wrong.** It was added after a real Windows machine failed here with
    `PermissionError` despite icacls exiting 0, on the guess that Windows
    Defender was briefly locking a file it had just watched an unsigned,
    freshly-SmartScreen-flagged process re-ACL. Retrying changed nothing: the
    failure reproduced on every attempt, on a brand-new file, exhausting the
    full backoff every time. Deterministic is not transient, and that ruled the
    theory out.

    The actual cause was `windows_acl_argv()` silently emitting no grant for
    the calling user at all — see its docstring. This is kept because a bounded
    retry around a filesystem call that another process may momentarily hold is
    cheap and occasionally right, but it is not load-bearing, and it should not
    be read as evidence that anything here is racy.
    """
    delay_iter = iter(_REOPEN_RETRY_SECONDS)
    while True:
        try:
            return os.open(path, flags, 0o600)
        except PermissionError:
            delay = next(delay_iter, None)
            if delay is None:
                raise
            time.sleep(delay)


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
        return _reopen_after_acl_change(path, flags)

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
        principal = windows_principal()
        if principal is None:
            # Refusing beats carrying on. /inheritance:r has stripped every
            # inherited permission by the time this matters, so an ACL with no
            # entry for the caller does not fail — it succeeds at locking them
            # out of their own token. See windows_acl_argv().
            raise ConfigPermissionError(
                f"could not determine which account to grant {path} to "
                f"(neither a SID from `whoami /user` nor %USERNAME%). Refusing "
                f"to write the agent token to a file this account would not be "
                f"able to read back."
            )
        try:
            result = subprocess.run(  # noqa: S603
                windows_acl_argv(path, principal=principal),
                capture_output=True,
                text=True,
                check=False,
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
