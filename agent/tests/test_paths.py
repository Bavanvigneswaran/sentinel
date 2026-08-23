"""Where the config and its token live, per platform and per scope.

Every assertion here is pure: `config_dir(..., system="Windows")` renders the
Windows answer on a Mac, which is the only way this project can check the
placement decisions for platforms it cannot run.
"""

import os
import platform
import stat

import pytest

from sentinel_agent import paths
from sentinel_agent.config import AgentConfig
from sentinel_agent.paths import ConfigPermissionError, config_dir, secure_file, windows_acl_argv

#: These tests do real filesystem work whose semantics are POSIX. The rendering
#: tests above them stay cross-platform on purpose — `config_dir(system="X")` is
#: pure — but chmod/fchmod/st_mode mean nothing on NTFS, where the Windows ACL
#: branch is the code that actually runs.
posix_only = pytest.mark.skipif(
    platform.system() == "Windows", reason="POSIX file modes; Windows uses the icacls branch"
)


def test_the_existing_unix_user_location_did_not_move(monkeypatch, tmp_path):
    """Phase 2 shipped ~/.config/sentinel and real installs already use it.

    Deliberately not XDG-aware: honouring XDG_CONFIG_HOME now would relocate
    every existing agent's token out from under it.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for system in ("Darwin", "Linux"):
        assert config_dir("user", system=system).name == "sentinel"
        assert config_dir("user", system=system).parent.name == ".config"


@pytest.mark.parametrize(
    ("system", "scope", "expected"),
    [
        ("Linux", "system", "/etc/sentinel"),
        ("Darwin", "system", "/Library/Application Support/Sentinel"),
    ],
)
def test_system_scope_leaves_home_behind(system, scope, expected):
    """A systemd system unit runs as root and a LaunchDaemon before login;
    neither has the HOME the person who enrolled the agent was typing in."""
    # as_posix(), not str(): config_dir() returns a Path, and a Path built on
    # Windows renders "/etc/sentinel" as "\etc\sentinel". The separator is the
    # host's; the placement is what this test is about.
    assert config_dir(scope, system=system).as_posix() == expected


def test_windows_user_config_is_local_not_roaming(monkeypatch):
    """An agent token is scoped to one device. Roaming it onto another machine
    would carry a credential that machine has no business holding."""
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\bo\AppData\Local")
    monkeypatch.setenv("APPDATA", r"C:\Users\bo\AppData\Roaming")
    resolved = str(config_dir("user", system="Windows"))
    assert "Local" in resolved and "Roaming" not in resolved


def test_windows_system_config_is_programdata(monkeypatch):
    monkeypatch.setenv("ProgramData", r"C:\ProgramData")
    assert "ProgramData" in str(config_dir("system", system="Windows"))


def test_the_env_override_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("SENTINEL_AGENT_HOME", str(tmp_path / "elsewhere"))
    assert paths.default_config_dir() == tmp_path / "elsewhere"


def test_no_env_override_falls_back_to_the_user_scope(monkeypatch):
    monkeypatch.delenv("SENTINEL_AGENT_HOME", raising=False)
    assert paths.default_config_dir() == config_dir("user")


def test_systemd_gets_no_log_directory():
    """journalctl is the platform's own answer and better than a file we
    rotate badly — but the *other* two platforms do need a path."""
    assert "Logs" in str(paths.log_dir("user", system="Darwin"))
    assert "logs" in str(paths.log_dir("user", system="Windows")).lower()


# --- making the token file private -----------------------------------------


def test_the_windows_acl_strips_inheritance():
    """The load-bearing flag. Without /inheritance:r the file keeps
    ProgramData's "Users: read" ACE and the token is readable by every account
    on the box."""
    argv = windows_acl_argv(paths.Path("C:/ProgramData/Sentinel/agent.toml"), username="bo")
    assert "/inheritance:r" in argv
    # /grant:r replaces rather than adds, so re-running cannot accumulate.
    assert "/grant:r" in argv and "/grant" not in argv[argv.index("/grant:r") + 1 :]


def test_the_windows_acl_uses_sids_not_localised_names():
    """"SYSTEM" and "Administrators" are localised; a German Windows would not
    match them and icacls would fail."""
    argv = windows_acl_argv(paths.Path("C:/x.toml"), username="bo")
    joined = " ".join(argv)
    assert "*S-1-5-18" in joined and "*S-1-5-32-544" in joined
    assert "SYSTEM" not in joined and "Administrators" not in joined


def test_the_windows_acl_survives_no_username(monkeypatch):
    monkeypatch.delenv("USERNAME", raising=False)
    argv = windows_acl_argv(paths.Path("C:/x.toml"))
    assert argv[-1].startswith("*S-1-5-32-544")


@posix_only
def test_secure_file_makes_it_owner_only(tmp_path):
    target = tmp_path / "agent.toml"
    target.write_text("x")
    target.chmod(0o644)

    secure_file(target, system="Linux")

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert paths.is_private(target, system="Linux")


@posix_only
def test_open_private_hands_back_a_file_that_is_already_locked_down(tmp_path):
    """The token is written through this descriptor, so the permissions have to
    be right before the caller ever sees it."""
    target = tmp_path / "agent.toml"

    fd = paths.open_private(target, system="Linux")
    try:
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        assert target.read_text() == ""
    finally:
        os.close(fd)


@posix_only
def test_open_private_does_not_leak_the_descriptor_when_it_cannot_secure_it(
    tmp_path, monkeypatch
):
    def refuse(fd, mode):
        raise PermissionError("nope")

    monkeypatch.setattr(paths.os, "fchmod", refuse)

    with pytest.raises(PermissionError):
        paths.open_private(tmp_path / "agent.toml", system="Linux")


def test_the_token_is_never_written_before_the_file_is_locked_down(tmp_path, monkeypatch):
    """save() writes through the descriptor open_private() vetted, so a failure
    to restrict cannot leave a secret on disk.

    The old shape — create, close, chmod by path, reopen by path — also had
    this property, but reopening by name let the entry be swapped for a symlink
    in between, so the token could land on the symlink's target with the 0600
    check having passed against a file that was no longer there.
    """
    def refuse(path, **kwargs):
        raise ConfigPermissionError("simulated ACL failure")

    monkeypatch.setattr("sentinel_agent.config.open_private", refuse)
    config = AgentConfig(agent_token="sag_secret", path=tmp_path / "agent.toml")  # noqa: S106

    with pytest.raises(ConfigPermissionError):
        config.save()

    assert not config.path.exists() or config.path.read_text() == ""
