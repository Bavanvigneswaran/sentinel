"""The three service definitions.

Every renderer is pure, so the systemd unit and the Windows task XML are
asserted here on whatever machine the suite runs on. That is the point: this
project is developed on a Mac, and a Linux unit or a Windows task that is only
inspected by eye is exactly the kind of thing that looks right and fails on the
target.

Actually registering anything with launchd/systemd/Task Scheduler is a system
side effect and stays out of the suite.
"""

import shlex
import xml.dom.minidom
from pathlib import Path

import pytest

from sentinel_agent.cli import build_parser
from sentinel_agent.service import (
    ServiceError,
    agent_command,
    base,
    installer_for,
    supported_scopes,
    systemd,
    windows,
)

CONFIG = Path("/etc/sentinel/agent.toml")


def _parse_agent_argv(argv: list[str]):
    """Feed argv back through the real parser, minus the executable prefix."""
    return build_parser().parse_args(argv[argv.index("--config") :])


# --- shared command construction -------------------------------------------


def test_config_precedes_the_subcommand_on_every_platform():
    """--config is a global option declared before the subparser. `run --config X`
    fails to parse, and a service manager just retries the failure until it
    gives up — a genuinely confusing symptom, so assert the parse, not the
    presence of the strings."""
    args = _parse_agent_argv(agent_command(CONFIG))
    assert args.command == "run"
    assert args.config == str(CONFIG)


def test_a_packaged_build_is_its_own_executable(monkeypatch):
    """sys.executable *is* the agent in a PyInstaller build; appending
    `-m sentinel_agent.cli` to it would be nonsense."""
    monkeypatch.setattr(base.sys, "frozen", True, raising=False)
    monkeypatch.setattr(base.sys, "executable", "/opt/sentinel/sentinel-agent")

    assert base.agent_executable() == ["/opt/sentinel/sentinel-agent"]


def test_a_source_install_uses_the_console_script(monkeypatch):
    monkeypatch.setattr(base.sys, "frozen", False, raising=False)
    assert base.agent_executable()[0].endswith(("sentinel-agent", "cli"))


def test_a_binary_left_in_downloads_is_flagged(monkeypatch, tmp_path):
    """A service pointed at a file the user later tidies away fails silently at
    the next boot."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    binary = downloads / "sentinel-agent"
    binary.write_text("")
    monkeypatch.setattr(base.sys, "frozen", True, raising=False)
    monkeypatch.setattr(base.sys, "executable", str(binary))

    assert "temporary location" in (base.check_executable_location() or "")


def test_a_source_checkout_is_never_flagged(monkeypatch):
    monkeypatch.setattr(base.sys, "frozen", False, raising=False)
    assert base.check_executable_location() is None


# --- systemd ----------------------------------------------------------------


def test_the_systemd_unit_starts_the_agent_with_its_config():
    unit = systemd.build_unit(CONFIG, "system")
    exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    args = _parse_agent_argv(shlex.split(exec_start.removeprefix("ExecStart=")))

    assert args.command == "run"
    assert args.config == str(CONFIG)


def test_a_config_path_with_a_space_survives_execstart():
    """systemd word-splits ExecStart. A home directory with a space in it —
    which macOS and Windows users create constantly and then carry to a Linux
    box — would otherwise arrive as two arguments."""
    spaced = Path("/home/my user/.config/sentinel/agent.toml")
    unit = systemd.build_unit(spaced, "user")
    exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))

    args = _parse_agent_argv(shlex.split(exec_start.removeprefix("ExecStart=")))
    assert args.config == str(spaced)


def test_network_online_not_merely_network_target():
    """The agent's first act is an outbound WSS connection. network.target only
    means "the stack is configured", not "a route exists"."""
    unit = systemd.build_unit(CONFIG, "system")
    assert "After=network-online.target" in unit
    assert "Wants=network-online.target" in unit


def test_netlink_is_allowed_through_the_address_family_filter():
    """psutil's per-NIC counters go through getifaddrs(), which is a netlink
    socket on Linux. Omitting AF_NETLINK produces an agent that connects fine
    and reports no network metrics at all."""
    unit = systemd.build_unit(CONFIG, "system")
    families = next(
        line for line in unit.splitlines() if line.startswith("RestrictAddressFamilies=")
    )
    assert "AF_NETLINK" in families
    assert "AF_INET" in families and "AF_INET6" in families


def test_the_process_table_is_not_hidden_from_the_agent():
    """ProtectProc=invisible hides /proc/<pid>/ for processes we do not own,
    which is exactly what the top-processes collector reads. A metrics agent is
    the one service that legitimately needs the process table."""
    assert "ProtectProc" not in systemd.build_unit(CONFIG, "system")


def test_the_system_unit_is_root_in_name_only():
    """An empty capability bounding set means uid 0 without CAP_DAC_OVERRIDE —
    it can read its own config and nothing it has no DAC right to."""
    unit = systemd.build_unit(CONFIG, "system")
    assert "CapabilityBoundingSet=" in unit
    assert "NoNewPrivileges=yes" in unit


def test_protecthome_is_system_scope_only():
    """A user unit's config lives under $HOME; ProtectHome would hide it from
    the very process that must read it."""
    assert "ProtectHome=yes" in systemd.build_unit(CONFIG, "system")
    assert "ProtectHome" not in systemd.build_unit(CONFIG, "user")


def test_the_install_target_matches_the_scope():
    assert "WantedBy=multi-user.target" in systemd.build_unit(CONFIG, "system")
    assert "WantedBy=default.target" in systemd.build_unit(CONFIG, "user")


def test_restarts_are_throttled():
    """Restart=always without a delay spins on a crash loop."""
    unit = systemd.build_unit(CONFIG, "user")
    assert "Restart=always" in unit
    assert "RestartSec=10" in unit


def test_the_user_unit_and_system_unit_go_to_different_paths():
    assert str(systemd.unit_path("system")).startswith("/etc/systemd/system")
    assert "systemd/user" in str(systemd.unit_path("user"))


# --- Windows Task Scheduler -------------------------------------------------


def _task(scope="user", config=CONFIG, **kwargs):
    # S318: the "untrusted data" here is a document this repo just rendered,
    # parsed to prove it is well-formed. defusedxml is not an agent dependency.
    return xml.dom.minidom.parseString(  # noqa: S318
        windows.build_task_xml(config, scope, **kwargs).encode("utf-16")
    )


def _windows_argv(doc):
    """Split <Arguments> the way Windows does, not the way a POSIX shell does.

    shlex's default mode treats a backslash as an escape, which turns every
    Windows path into mush. CommandLineToArgvW only honours quotes.
    """
    raw = _text(doc, "Arguments")
    parts = [p.strip('"') for p in shlex.split(raw, posix=False)]
    return [_text(doc, "Command"), *parts]


def _text(doc, tag):
    nodes = doc.getElementsByTagName(tag)
    return nodes[0].firstChild.nodeValue if nodes and nodes[0].firstChild else None


def test_the_task_xml_is_well_formed():
    """schtasks rejects a malformed file with an unhelpful "The task XML is
    malformed" and no line number."""
    _task("system")
    _task("user", user_id="WS\\bo")


def test_the_task_runs_the_agent_with_its_config():
    doc = _task("system", config=Path(r"C:\ProgramData\Sentinel\agent.toml"))
    args = _parse_agent_argv(_windows_argv(doc))

    assert args.command == "run"
    assert args.config == r"C:\ProgramData\Sentinel\agent.toml"


def test_a_windows_path_with_a_space_is_quoted_in_arguments():
    """<Command> is not re-split but <Arguments> is, so only the latter needs
    quoting — which is the whole reason this uses /XML rather than /TR."""
    spaced = Path(r"C:\Users\Bo Bo\AppData\Local\Sentinel\agent.toml")
    doc = _task("user", config=spaced, user_id="WS\\bo")
    args = _parse_agent_argv(_windows_argv(doc))

    assert args.config == str(spaced)


def test_the_agent_does_not_stop_when_a_laptop_is_unplugged():
    """Both settings default to true. Left alone, the agent stops the moment
    the charger comes out and looks exactly like the machine going offline."""
    doc = _task("user", user_id="WS\\bo")
    assert _text(doc, "StopIfGoingOnBatteries") == "false"
    assert _text(doc, "DisallowStartIfOnBatteries") == "false"


def test_the_agent_is_not_stopped_when_the_idle_period_ends():
    assert _text(_task("user", user_id="WS\\bo"), "StopOnIdleEnd") == "false"


def test_there_is_no_execution_time_limit():
    """The default is 3 days, after which Task Scheduler terminates a perfectly
    healthy long-running agent."""
    assert _text(_task("system"), "ExecutionTimeLimit") == "PT0S"


def test_the_task_asks_for_no_elevation():
    """Same reasoning as launchd's LaunchAgent and systemd's empty capability
    set: the agent genuinely needs no privileges."""
    assert _text(_task("system"), "RunLevel") == "LeastPrivilege"
    assert _text(_task("user", user_id="WS\\bo"), "RunLevel") == "LeastPrivilege"


def test_system_scope_boots_as_localsystem_by_sid():
    doc = _task("system")
    assert doc.getElementsByTagName("BootTrigger")
    assert not doc.getElementsByTagName("LogonTrigger")
    # The name "SYSTEM" is localised; the SID is not.
    assert _text(doc, "UserId") == windows.LOCAL_SYSTEM_SID


def test_user_scope_triggers_on_logon():
    doc = _task("user", user_id="WS\\bo")
    assert doc.getElementsByTagName("LogonTrigger")
    assert not doc.getElementsByTagName("BootTrigger")
    assert _text(doc, "UserId") == "WS\\bo"


def test_a_username_with_xml_metacharacters_cannot_break_the_document():
    """USERNAME is whatever the machine's account is called, and it reaches the
    document as text."""
    doc = _task("user", user_id="WS\\a&b<c>")
    assert _text(doc, "UserId") == "WS\\a&b<c>"


def test_create_replaces_an_existing_task():
    """/F, so reinstalling over a previous version does not fail."""
    assert "/F" in windows.build_create_argv(Path("t.xml"), "user")


def test_creating_a_boot_task_names_localsystem():
    argv = windows.build_create_argv(Path("t.xml"), "system")
    assert "/RU" in argv and windows.LOCAL_SYSTEM_SID in argv
    assert "/RU" not in windows.build_create_argv(Path("t.xml"), "user")


# --- dispatch ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("system", "module"),
    [("Darwin", "launchd"), ("Linux", "systemd"), ("Windows", "windows")],
)
def test_each_platform_resolves_to_its_own_installer(system, module):
    assert installer_for(system).__name__.endswith(module)


def test_an_unknown_platform_says_so_rather_than_guessing():
    with pytest.raises(ServiceError, match="FreeBSD"):
        installer_for("FreeBSD")


def test_macos_offers_no_system_scope():
    """Not an oversight: a LaunchDaemon is root-requiring code that cannot be
    exercised from a test suite, and shipping it unverified is what this
    project's status notes exist to prevent."""
    assert supported_scopes("Darwin") == ("user",)
    assert "system" in supported_scopes("Linux")
    assert "system" in supported_scopes("Windows")


def test_the_macos_installer_refuses_system_scope_with_an_actionable_error():
    from sentinel_agent.service import launchd

    with pytest.raises(ServiceError, match="LaunchDaemon"):
        launchd.build_plist(CONFIG, "system")


# --- logging on a platform whose service manager captures nothing -----------


def test_the_windows_task_tells_the_agent_where_to_log():
    """launchd redirects stdout in the plist and systemd owns the journal, but
    a Task Scheduler task's output is discarded outright — so without this the
    one platform where the agent is hardest to debug is the only one with no
    log at all."""
    doc = _task("system")
    args = _parse_agent_argv(_windows_argv(doc))

    assert args.log_file is not None
    assert args.log_file.endswith("agent.log")
    assert args.command == "run"


def test_the_other_platforms_do_not_pass_a_log_file():
    """They already have somewhere for output to go, and a second copy would
    just be a file nobody rotates."""
    assert "--log-file" not in systemd.build_unit(CONFIG, "system")
    assert "--log-file" not in agent_command(CONFIG)


def test_a_log_file_still_leaves_the_config_before_the_subcommand():
    args = _parse_agent_argv(agent_command(CONFIG, Path("/var/log/sentinel/agent.log")))
    assert args.command == "run"
    assert args.config == str(CONFIG)
    assert args.log_file == "/var/log/sentinel/agent.log"
