"""`sentinel-agent run` and plaintext to a remote host.

Enrollment has always refused it — that exchange carries the one-time code and
the long-lived token it becomes. `run` only warned, on the reasoning that
killing a working monitor over a transport choice its operator had already made
was the wrong trade.

That had the exposure backwards. Enrollment sends the credential once; `run`
replays the same agent token in the handshake on *every reconnect*, and the
agent reconnects with backoff, so an outage hands anyone on the path a fresh
copy every few seconds — along with every metric the machine reports. `run` is
the higher-exposure half of the pair.
"""

from pathlib import Path

import pytest

from sentinel_agent.cli import build_parser, main
from sentinel_agent.config import AgentConfig, requires_tls


@pytest.fixture
def enrolled(tmp_path, monkeypatch):
    """A config that is complete apart from where it points."""

    def _configure(server_url: str) -> Path:
        path = tmp_path / "agent.toml"
        config = AgentConfig(
            server_url=server_url,
            agent_token="sag_" + "t" * 40,
            device_id="11111111-1111-1111-1111-111111111111",
            path=path,
        )
        config.save()
        return path

    return _configure


def _run(config_path: Path, *extra: str) -> int:
    return main(["--config", str(config_path), "run", *extra])


def test_run_refuses_a_remote_plaintext_server(enrolled, capsys):
    code = _run(enrolled("http://monitoring.example:8000"))
    assert code == 1
    assert "Refusing to connect" in capsys.readouterr().err


def test_the_refusal_names_both_ways_out(enrolled, capsys):
    """An operator reading this has to know what to do next."""
    _run(enrolled("http://monitoring.example:8000"))
    err = capsys.readouterr().err
    assert "https://" in err
    assert "--insecure" in err


def test_insecure_allows_it(enrolled, monkeypatch):
    """The escape hatch is the same one enrollment has, so an operator who
    genuinely means it on a trusted LAN types it once."""
    started = []
    monkeypatch.setattr(
        "sentinel_agent.cli.asyncio.run", lambda coro: (started.append(True), coro.close())
    )
    assert _run(enrolled("http://monitoring.example:8000"), "--insecure") == 0
    assert started == [True]


def test_loopback_needs_no_flag(enrolled, monkeypatch):
    """Every developer runs against localhost. Requiring the flag there would
    teach everyone to type it by reflex."""
    started = []
    monkeypatch.setattr(
        "sentinel_agent.cli.asyncio.run", lambda coro: (started.append(True), coro.close())
    )
    assert _run(enrolled("http://localhost:8000")) == 0
    assert started == [True]


def test_https_needs_no_flag(enrolled, monkeypatch):
    started = []
    monkeypatch.setattr(
        "sentinel_agent.cli.asyncio.run", lambda coro: (started.append(True), coro.close())
    )
    assert _run(enrolled("https://sentinel.example")) == 0
    assert started == [True]


def test_the_parser_accepts_insecure_on_run():
    args = build_parser().parse_args(["run", "--insecure"])
    assert args.command == "run"
    assert args.insecure is True


def test_run_defaults_to_secure():
    assert build_parser().parse_args(["run"]).insecure is False


def test_requires_tls_agrees_with_the_gate():
    """The gate and the enrollment refusal must read the same predicate, or the
    two halves of the product disagree about what "remote" means."""
    assert requires_tls("http://monitoring.example:8000") is True
    assert requires_tls("http://localhost:8000") is False
    assert requires_tls("http://127.0.0.1:8000") is False
    assert requires_tls("https://sentinel.example") is False
