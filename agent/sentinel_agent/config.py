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

#: The only two values the server's `Sample.resolution_seconds` accepts —
#: app/schemas/protocol.py declares it `Literal[1, 10]`, and that schema is the
#: contract both sides import (CLAUDE.md's Phase 2 invariants).
#:
#: Both intervals below are stamped onto samples as their resolution: the
#: sample interval in live mode, the push interval when a window is collapsed
#: (see runner.py's `_build_batch`). Anything else validates cleanly here and
#: is then rejected by the server as `invalid_frame` with `retryable=false`,
#: which the transport correctly treats as fatal — so an agent configured with,
#: say, a 30s push interval connects, handshakes, pushes once and exits, and
#: the only clue is "frame failed validation". Checking it where the value is
#: set turns that into a sentence naming the field.
PROTOCOL_RESOLUTIONS = (1, 10)

#: How many samples to hold when the socket is down. At 1s sampling this is a
#: little over an hour, which matches the server's accepted sample age — buffering
#: longer would only produce rows the server will reject.
DEFAULT_BUFFER_SIZE = 3600


class ConfigError(Exception):
    pass


#: Characters a TOML basic string may not carry literally, and what they become.
#: `\\` must be first or it would re-escape the backslashes the others add.
_TOML_ESCAPES = (
    ("\\", "\\\\"),
    ('"', '\\"'),
    ("\n", "\\n"),
    ("\r", "\\r"),
    ("\t", "\\t"),
)


def toml_string(value: str) -> str:
    """`value` as a TOML basic string, quotes included.

    save() used to interpolate values straight into `key = "{value}"`. Every
    value written there is user-supplied — `--server`, and the latency targets
    — so a single quote or backslash produced a config file that parsed on the
    way out and not on the way back in: `enroll` succeeded, wrote the token,
    and `run` then died in tomllib. Exactly the failure mode the cp1252
    encoding bug had, reached by a different route.
    """
    escaped = value
    for raw, replacement in _TOML_ESCAPES:
        escaped = escaped.replace(raw, replacement)
    # Remaining control characters have no short escape and must be \uXXXX.
    # The tuple above has already turned every control character TOML gives a
    # short escape to into two printable ones, so anything still below U+0020
    # has no shorthand and must go out as \uXXXX.
    escaped = "".join(c if c >= " " else f"\\u{ord(c):04X}" for c in escaped)
    return f'"{escaped}"'


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
            f"server_url = {toml_string(self.server_url)}",
        ]
        if self.agent_token:
            lines.append(f"agent_token = {toml_string(self.agent_token)}")
        if self.device_id:
            lines.append(f"device_id = {toml_string(self.device_id)}")
        lines += [
            f"sample_interval_seconds = {self.sample_interval_seconds}",
            f"push_interval_seconds = {self.push_interval_seconds}",
            f"buffer_size = {self.buffer_size}",
            "latency_targets = ["
            + ", ".join(toml_string(t) for t in self.latency_targets)
            + "]",
            f"collect_processes = {str(self.collect_processes).lower()}",
            "",
        ]

        # The file is private before a single byte of the token is written, and
        # what gets written to is the descriptor whose permissions were checked
        # — never the path re-resolved a second time. See paths.open_private().
        # encoding="utf-8" is load-bearing, not tidiness. Without it Python
        # writes in the locale encoding, which is cp1252 on a default Windows
        # install, and the em-dash in the header comment above lands as a bare
        # 0x97 byte. tomllib only accepts UTF-8, so `enroll` would write a
        # config that `run` could never read back — the agent bricked itself on
        # the one platform this project cannot test locally. Found by the CI
        # matrix's first Windows run.
        with os.fdopen(open_private(self.path), "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))

    def validate_intervals(self) -> None:
        """Fail fast on a cadence the server will refuse.

        Called before the agent connects rather than at load(), so `status`
        still renders a broken config instead of refusing to describe it —
        diagnosing is the one thing that has to keep working.
        """
        for field_name, value in (
            ("sample_interval_seconds", self.sample_interval_seconds),
            ("push_interval_seconds", self.push_interval_seconds),
        ):
            if value not in PROTOCOL_RESOLUTIONS:
                allowed = " or ".join(str(v) for v in PROTOCOL_RESOLUTIONS)
                raise ConfigError(
                    f"{field_name} = {value} in {self.path} is not a resolution the "
                    f"server accepts (must be {allowed}). Every sample is stamped "
                    f"with one of these two, and the server rejects any other value "
                    f"as an invalid frame."
                )
        if self.push_interval_seconds < self.sample_interval_seconds:
            raise ConfigError(
                f"push_interval_seconds ({self.push_interval_seconds}) is shorter than "
                f"sample_interval_seconds ({self.sample_interval_seconds}) in "
                f"{self.path}: there would be nothing new to send on most pushes."
            )

    def require_token(self) -> str:
        if not self.agent_token:
            raise ConfigError(
                f"no agent token in {self.path}. Run `sentinel-agent enroll --code <CODE>` first."
            )
        return self.agent_token
