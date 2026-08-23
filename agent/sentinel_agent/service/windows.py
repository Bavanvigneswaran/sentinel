"""Windows service installation — via Task Scheduler, not the Service Control Manager.

This is the one place across the three platforms where the obvious answer is
the wrong one, so the reasoning is written down rather than implied.

A Windows *service* is not merely a background process: the SCM starts it and
then expects it to call back into `StartServiceCtrlDispatcher` within roughly
30 seconds and to answer control requests thereafter. A PyInstaller-built
console binary does none of that, so `sc.exe create` produces a service that
Windows starts, waits for, declares "did not respond in a timely fashion"
(error 1053), and kills. Making the agent a real service means adding pywin32,
a service-entry shim, and a second frozen entry point — a genuine option, but
one that must be built and tested on Windows, which this project cannot do
from the machine it is developed on.

Task Scheduler is a first-class, supported Windows mechanism for exactly this:
it starts a plain executable at boot or logon, restarts it on failure, and
survives logoff. What it costs is honesty in the docs — `services.msc` will not
list the agent; Task Scheduler will.

Registration goes through `/XML` rather than `/TR`. The `/TR` form takes the
entire command as one string and needs nested-quote escaping that breaks the
moment a path contains a space (`C:\\Program Files\\...`), and more importantly
it cannot express the settings that decide whether this agent actually keeps
running. Task Scheduler's defaults stop a task when the machine goes on
battery and when the idle period ends — an agent that goes deaf the moment a
laptop is unplugged is the same failure Phase 10b's `specialUse` foreground
service type exists to prevent.
"""

from __future__ import annotations

import os
import subprocess  # noqa: S404 — schtasks is the supported interface
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from sentinel_agent.paths import Scope, log_dir
from sentinel_agent.service.base import InstallResult, ServiceError, agent_command

TASK_NAME = "Sentinel Agent"

#: The LocalSystem SID. Used instead of the name "SYSTEM" because that name is
#: localised and a German or French Windows will not match it.
LOCAL_SYSTEM_SID = "S-1-5-18"


def current_user() -> str:
    """`DOMAIN\\user`, as Task Scheduler wants it."""
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    return f"{domain}\\{user}" if domain and user else user


def build_task_xml(
    config_path: Path,
    scope: Scope = "user",
    *,
    user_id: str | None = None,
    log_file: Path | None = None,
) -> str:
    """Render the Task Scheduler definition. Pure — assertable from a Mac.

    Task Scheduler wants the executable and its arguments split across
    `<Command>` and `<Arguments>`, which is precisely why this form avoids the
    `/TR` quoting problem: neither field is word-split, so a path with a space
    in it needs no quoting at all.
    """
    argv = agent_command(config_path, log_file or (log_dir(scope, system="Windows") / "agent.log"))
    command, arguments = argv[0], argv[1:]

    if scope == "system":
        principal_user = LOCAL_SYSTEM_SID
        logon_type = "ServiceAccount"
        trigger = "    <BootTrigger>\n      <Enabled>true</Enabled>\n    </BootTrigger>"
    else:
        principal_user = user_id or current_user()
        logon_type = "InteractiveToken"
        trigger = (
            "    <LogonTrigger>\n"
            "      <Enabled>true</Enabled>\n"
            f"      <UserId>{xml_escape(principal_user)}</UserId>\n"
            "    </LogonTrigger>"
        )

    # Arguments is one string here; each element is already a separate argv
    # entry to Task Scheduler's own parser, but it re-splits on spaces, so the
    # config path is quoted. The executable in <Command> is not re-split.
    quoted_arguments = " ".join(f'"{a}"' if " " in a else a for a in arguments)

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Sentinel monitoring agent. Collects this machine's real system
metrics and pushes them outbound over WSS. Needs no inbound ports and no
elevated privileges.</Description>
    <URI>\\{xml_escape(TASK_NAME)}</URI>
  </RegistrationInfo>
  <Triggers>
{trigger}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{xml_escape(principal_user)}</UserId>
      <LogonType>{logon_type}</LogonType>
      <!-- The agent needs no elevation. TCP-connect latency probes instead of
           raw ICMP sockets was a Phase 2 decision made exactly so this could
           say LeastPrivilege. -->
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <!-- Both default to true. Left alone, a laptop agent stops the moment the
         charger comes out and never restarts, which looks exactly like the
         machine going offline. -->
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <!-- Also defaults to true: the task would be stopped when the idle
           period ends. -->
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <DisallowStartOnRemoteAppSession>false</DisallowStartOnRemoteAppSession>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
    <!-- PT0S means no limit. The default is 3 days, after which Task Scheduler
         would terminate a perfectly healthy long-running agent. -->
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{xml_escape(command)}</Command>
      <Arguments>{xml_escape(quoted_arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def build_create_argv(xml_file: Path, scope: Scope = "user") -> list[str]:  # noqa: ARG001
    """The `schtasks /Create` invocation. Pure, so the flags can be asserted.

    Deliberately no `/RU`, for either scope. With `/XML` the principal in the
    document is authoritative, and the document already names LocalSystem by
    SID with `LogonType=ServiceAccount` — which the Task Scheduler schema
    documents as valid and which needs no password. `/RU`, by contrast,
    documents only `""`, `"NT AUTHORITY\\SYSTEM"` and `"SYSTEM"` for the
    system account: a raw SID is not a documented value there, so passing one
    risks "The user name or password is incorrect" on a machine this project
    has never been able to test against. Registering a LocalSystem task still
    requires an elevated prompt; Windows enforces that with or without the flag.
    """
    return ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_file), "/F"]


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


def install(config_path: Path, scope: Scope = "user") -> InstallResult:
    logs = log_dir(scope, system="Windows")
    log_file = logs / "agent.log"
    logs.mkdir(parents=True, exist_ok=True)
    xml = build_task_xml(config_path, scope, log_file=log_file)

    # schtasks /XML requires the file to be UTF-16 with a BOM when the
    # declaration says so, and it rejects the file outright otherwise with an
    # unhelpful "The task XML is malformed".
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="w", suffix=".xml", encoding="utf-16", delete=False
    )
    try:
        with handle:
            handle.write(xml)
        result = _run(build_create_argv(Path(handle.name), scope))
    finally:
        Path(handle.name).unlink(missing_ok=True)

    if result.returncode != 0:
        hint = (
            " (run this from an Administrator prompt)"
            if scope == "system"
            else ""
        )
        raise ServiceError(
            f"schtasks /Create failed{hint}: {(result.stderr or result.stdout).strip()}"
        )

    # Registering a logon/boot trigger does not start it now.
    _run(["schtasks", "/Run", "/TN", TASK_NAME])

    return InstallResult(
        scope=scope,
        unit_path=Path(f"\\{TASK_NAME}"),
        logs=f'{log_file}  (task listed under "{TASK_NAME}" in Task Scheduler)',
    )


def uninstall(scope: Scope = "user") -> bool:  # noqa: ARG001 — one task, either scope
    _run(["schtasks", "/End", "/TN", TASK_NAME])
    result = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    return result.returncode == 0


def status(scope: Scope = "user") -> str:  # noqa: ARG001
    result = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])
    if result.returncode != 0:
        return "not installed"
    for line in result.stdout.splitlines():
        if line.strip().lower().startswith("status:"):
            return line.split(":", 1)[1].strip()
    return "installed"


__all__ = [
    "LOCAL_SYSTEM_SID",
    "TASK_NAME",
    "build_create_argv",
    "build_task_xml",
    "current_user",
    "install",
    "status",
    "uninstall",
]
