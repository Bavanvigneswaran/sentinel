"""Agent configuration.

Lives in a TOML file next to the agent token. The token is the only secret
here, so the file is written 0600 and never logged.
"""

from __future__ import annotations

import ipaddress
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sentinel_agent.paths import CONFIG_FILENAME, default_config_dir, open_private

# Resolved at import for the CLI's help text. `SENTINEL_AGENT_HOME` still wins;
# see paths.py for why a service is handed an explicit --config instead of
# re-deriving this from an environment it does not have.
DEFAULT_CONFIG_DIR = default_config_dir()
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / CONFIG_FILENAME

DEFAULT_SERVER = "http://localhost:8000"
DEFAULT_SAMPLE_INTERVAL = 1
DEFAULT_PUSH_INTERVAL = 10
#: How many samples to hold when the socket is down. At 1s sampling this is a
#: little over an hour, which matches the server's accepted sample age — buffering
#: longer would only produce rows the server will reject.
DEFAULT_BUFFER_SIZE = 3600


class ConfigError(Exception):
    pass


def is_loopback(url: str) -> bool:
    """True when `url` points at this machine.

    Used to decide whether plaintext HTTP is acceptable. Anything that is not
    provably loopback is treated as remote — an unresolvable or odd host is not
    given the benefit of the doubt, because the thing being protected is a
    long-lived credential.
    """
    host = urlsplit(url).hostname
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def requires_tls(url: str) -> bool:
    """True when talking to `url` in plaintext would expose the agent token.

    ARCHITECTURE.md has said since Phase 0 that it is `wss://` only outside
    localhost. Phase 11 is when that stops being advice: before it, the agent
    was something you ran against your own dev server; now it is a binary a
    stranger downloads and points at a URL they typed.
    """
    return not url.startswith("https://") and not is_loopback(url)


@dataclass
class AgentConfig:
    server_url: str = DEFAULT_SERVER
    agent_token: str | None = None
    device_id: str | None = None

    sample_interval_seconds: int = DEFAULT_SAMPLE_INTERVAL
    push_interval_seconds: int = DEFAULT_PUSH_INTERVAL
    buffer_size: int = DEFAULT_BUFFER_SIZE

    latency_targets: list[str] = field(default_factory=lambda: ["1.1.1.1:443"])
    collect_processes: bool = True

    path: Path = DEFAULT_CONFIG_PATH

    @property
    def ws_url(self) -> str:
        base = self.server_url.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :] + "/ws/agent"
        if base.startswith("http://"):
            return "ws://" + base[len("http://") :] + "/ws/agent"
        raise ConfigError(f"server_url must start with http:// or https://, got {base!r}")

    @property
    def enroll_url(self) -> str:
        return self.server_url.rstrip("/") + "/enroll"

    @classmethod
    def load(cls, path: Path | None = None) -> AgentConfig:
        path = path or DEFAULT_CONFIG_PATH
        if not path.exists():
            return cls(path=path)

        with path.open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)

        agent = data.get("agent", {})
        return cls(
            server_url=agent.get("server_url", DEFAULT_SERVER),
            agent_token=agent.get("agent_token"),
            device_id=agent.get("device_id"),
            sample_interval_seconds=int(
                agent.get("sample_interval_seconds", DEFAULT_SAMPLE_INTERVAL)
            ),
            push_interval_seconds=int(agent.get("push_interval_seconds", DEFAULT_PUSH_INTERVAL)),
            buffer_size=int(agent.get("buffer_size", DEFAULT_BUFFER_SIZE)),
            latency_targets=list(agent.get("latency_targets", ["1.1.1.1:443"])),
            collect_processes=bool(agent.get("collect_processes", True)),
            path=path,
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            "# Sentinel agent configuration.",
            "# Contains the agent token — keep this file readable only by its owner.",
            "",
            "[agent]",
            f'server_url = "{self.server_url}"',
        ]
        if self.agent_token:
            lines.append(f'agent_token = "{self.agent_token}"')
        if self.device_id:
            lines.append(f'device_id = "{self.device_id}"')
        lines += [
            f"sample_interval_seconds = {self.sample_interval_seconds}",
            f"push_interval_seconds = {self.push_interval_seconds}",
            f"buffer_size = {self.buffer_size}",
            "latency_targets = ["
            + ", ".join(f'"{t}"' for t in self.latency_targets)
            + "]",
            f"collect_processes = {str(self.collect_processes).lower()}",
            "",
        ]

        # The file is private before a single byte of the token is written, and
        # what gets written to is the descriptor whose permissions were checked
        # — never the path re-resolved a second time. See paths.open_private().
        with os.fdopen(open_private(self.path), "w") as handle:
            handle.write("\n".join(lines))

    def require_token(self) -> str:
        if not self.agent_token:
            raise ConfigError(
                f"no agent token in {self.path}. Run `sentinel-agent enroll --code <CODE>` first."
            )
        return self.agent_token
