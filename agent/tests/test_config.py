"""Agent configuration and the transport's backoff."""

import stat

import pytest

from sentinel_agent.config import AgentConfig, ConfigError
from sentinel_agent.transport.client import (
    INITIAL_BACKOFF_SECONDS,
    MAX_BACKOFF_SECONDS,
    next_backoff,
)


def test_defaults_are_usable_without_a_file(tmp_path):
    config = AgentConfig.load(tmp_path / "missing.toml")
    assert config.agent_token is None
    assert config.sample_interval_seconds == 1
    assert config.push_interval_seconds == 10


def test_a_saved_config_round_trips(tmp_path):
    original = AgentConfig(
        server_url="https://sentinel.example.com",
        agent_token="sag_secret",  # noqa: S106
        device_id="abc-123",
        latency_targets=["1.1.1.1:443", "gateway:80"],
        path=tmp_path / "agent.toml",
    )
    original.save()

    loaded = AgentConfig.load(original.path)
    assert loaded.server_url == "https://sentinel.example.com"
    assert loaded.agent_token == "sag_secret"  # noqa: S105
    assert loaded.device_id == "abc-123"
    assert loaded.latency_targets == ["1.1.1.1:443", "gateway:80"]


def test_the_config_file_is_not_readable_by_others(tmp_path):
    """It holds the agent token."""
    config = AgentConfig(agent_token="sag_secret", path=tmp_path / "agent.toml")  # noqa: S106
    config.save()

    mode = stat.S_IMODE(config.path.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_saving_twice_keeps_the_restrictive_mode(tmp_path):
    config = AgentConfig(agent_token="sag_secret", path=tmp_path / "agent.toml")  # noqa: S106
    config.save()
    config.agent_token = "sag_rotated"  # noqa: S105
    config.save()

    assert stat.S_IMODE(config.path.stat().st_mode) == 0o600
    assert AgentConfig.load(config.path).agent_token == "sag_rotated"  # noqa: S105


def test_websocket_urls_follow_the_server_scheme():
    assert AgentConfig(server_url="http://localhost:8000").ws_url == "ws://localhost:8000/ws/agent"
    assert (
        AgentConfig(server_url="https://sentinel.example.com").ws_url
        == "wss://sentinel.example.com/ws/agent"
    )


def test_a_trailing_slash_does_not_double_up():
    assert AgentConfig(server_url="http://localhost:8000/").ws_url.endswith("8000/ws/agent")


def test_an_unsupported_scheme_is_rejected():
    with pytest.raises(ConfigError):
        _ = AgentConfig(server_url="ftp://example.com").ws_url


def test_running_without_a_token_gives_an_actionable_error(tmp_path):
    config = AgentConfig(path=tmp_path / "agent.toml")
    with pytest.raises(ConfigError, match="enroll"):
        config.require_token()


# --- backoff ----------------------------------------------------------------


def test_backoff_grows_and_is_capped():
    assert next_backoff(0) <= INITIAL_BACKOFF_SECONDS
    assert next_backoff(3) > next_backoff(0)
    for attempt in range(0, 40):
        assert next_backoff(attempt) <= MAX_BACKOFF_SECONDS


def test_backoff_is_jittered():
    """Without jitter every agent that lost the same backend retries in
    lockstep and knocks it straight back over."""
    delays = {next_backoff(5) for _ in range(50)}
    assert len(delays) > 1, "backoff is deterministic"


def test_backoff_is_never_zero():
    assert all(next_backoff(a) > 0 for a in range(20))


# --- refusing to hand a credential to a plaintext connection ----------------


def test_a_remote_http_server_requires_tls():
    """Enrollment carries the single-use code *and* returns the long-lived
    token it becomes. Over http:// that hands anyone on the path a credential
    valid until somebody notices and revokes it."""
    from sentinel_agent.config import requires_tls

    assert requires_tls("http://sentinel.example.com")
    assert requires_tls("http://203.0.113.10:8000")
    assert not requires_tls("https://sentinel.example.com")


def test_localhost_over_http_is_still_fine():
    """Otherwise every developer would have to run TLS to use their own dev
    server, and the guard would just be turned off."""
    from sentinel_agent.config import requires_tls

    for url in (
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://[::1]:8000",
        "http://api.localhost:8000",
    ):
        assert not requires_tls(url), url


def test_an_unparseable_host_is_treated_as_remote():
    """Not given the benefit of the doubt: the thing being protected is a
    long-lived credential."""
    from sentinel_agent.config import is_loopback, requires_tls

    assert not is_loopback("http://")
    assert requires_tls("http://")


def test_a_lookalike_hostname_is_not_loopback():
    """`localhost.evil.com` resolves to whatever its owner wants."""
    from sentinel_agent.config import is_loopback

    assert not is_loopback("http://localhost.evil.com")
    assert not is_loopback("http://notlocalhost")


def test_enrolling_against_a_remote_http_server_is_refused(tmp_path):
    from sentinel_agent.enroll import EnrollmentError, enroll

    config = AgentConfig(server_url="http://sentinel.example.com", path=tmp_path / "a.toml")
    with pytest.raises(EnrollmentError, match="cleartext"):
        enroll(config, "X4T9-K2QM-7PDR")

    assert not config.path.exists(), "nothing should have been written"


def test_the_refusal_can_be_overridden_deliberately(tmp_path, monkeypatch):
    """A LAN test against a server with no certificate yet is legitimate — it
    just has to be asked for."""
    import httpx

    from sentinel_agent import enroll as enroll_mod

    def fake_post(url, **kwargs):
        return httpx.Response(
            201,
            json={"agent_token": "sag_x", "device_id": "d-1"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(enroll_mod.httpx, "post", fake_post)
    config = AgentConfig(server_url="http://sentinel.example.com", path=tmp_path / "a.toml")

    enroll_mod.enroll(config, "X4T9-K2QM-7PDR", allow_insecure=True)
    assert config.agent_token == "sag_x"  # noqa: S105
