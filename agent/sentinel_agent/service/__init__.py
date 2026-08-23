"""Picking the right service installer for the machine we are on.

Three platforms, three completely different registration mechanisms, one
interface: `build_*` renders the unit purely (so every platform's output is
assertable from every other platform's test run), `install`/`uninstall`/`status`
touch the system.

`SUPPORTED_SCOPES` is per-platform rather than global on purpose — see
launchd.py for why macOS has no system scope.
"""

from __future__ import annotations

import platform
from types import ModuleType

from sentinel_agent.paths import Scope
from sentinel_agent.service.base import (
    InstallResult,
    ServiceError,
    agent_command,
    agent_executable,
    check_executable_location,
    is_frozen,
)

#: What each platform's installer registers, for the CLI's own messages.
MECHANISM = {
    "Darwin": "launchd LaunchAgent",
    "Linux": "systemd unit",
    "Windows": "Task Scheduler task",
}

SUPPORTED_SCOPES: dict[str, tuple[Scope, ...]] = {
    "Darwin": ("user",),
    "Linux": ("user", "system"),
    "Windows": ("user", "system"),
}


def installer_for(system: str | None = None) -> ModuleType:
    """Import the installer module for `system`, or raise an actionable error."""
    system = system or platform.system()

    if system == "Darwin":
        from sentinel_agent.service import launchd

        return launchd
    if system == "Linux":
        from sentinel_agent.service import systemd

        return systemd
    if system == "Windows":
        from sentinel_agent.service import windows

        return windows

    raise ServiceError(
        f"no service installer for {system!r}. Run `sentinel-agent run` under "
        f"whatever supervisor this platform provides."
    )


def supported_scopes(system: str | None = None) -> tuple[Scope, ...]:
    return SUPPORTED_SCOPES.get(system or platform.system(), ())


def mechanism(system: str | None = None) -> str:
    return MECHANISM.get(system or platform.system(), "service")


__all__ = [
    "MECHANISM",
    "SUPPORTED_SCOPES",
    "InstallResult",
    "ServiceError",
    "agent_command",
    "agent_executable",
    "check_executable_location",
    "installer_for",
    "is_frozen",
    "mechanism",
    "supported_scopes",
]
