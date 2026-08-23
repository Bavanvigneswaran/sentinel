"""app/alerts/fcm.py: the envelope it builds, which failures retire a token,
and that an unconfigured server is a logged no-op rather than an error.

The HTTP call itself is never made — there is no Firebase project in the test
environment, and the point of these tests is the classification logic around
the call, not httpx.
"""

from __future__ import annotations

import pytest

from app.alerts import fcm
from app.config import Settings


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, environment="test", **overrides)


CONFIGURED = {
    "fcm_project_id": "sentinel-test",
    "fcm_service_account_file": "/nonexistent/service-account.json",
}


@pytest.fixture(autouse=True)
def _reset_credentials():
    fcm.reset_credentials_cache()
    yield
    fcm.reset_credentials_cache()


def test_unconfigured_is_not_configured(monkeypatch):
    monkeypatch.setattr(fcm, "get_settings", lambda: _settings())
    assert fcm.is_configured() is False


def test_a_project_id_alone_is_not_configured(monkeypatch):
    """Both halves are needed: the project id names the endpoint, the key file
    is what authorises against it."""
    monkeypatch.setattr(fcm, "get_settings", lambda: _settings(fcm_project_id="sentinel-test"))
    assert fcm.is_configured() is False


def test_both_halves_present_is_configured(monkeypatch):
    monkeypatch.setattr(fcm, "get_settings", lambda: _settings(**CONFIGURED))
    assert fcm.is_configured() is True


async def test_unconfigured_send_is_a_no_op(monkeypatch):
    """Same posture as an unset SMTP host or VAPID keypair: log it, return,
    never raise into the evaluator sweep."""
    monkeypatch.setattr(fcm, "get_settings", lambda: _settings())
    dead = await fcm.send_to_tokens(["tok"], title="t", body="b", data={"url": "/alerts"})
    assert dead == []


async def test_send_with_no_tokens_short_circuits(monkeypatch):
    monkeypatch.setattr(fcm, "get_settings", lambda: _settings(**CONFIGURED))
    assert await fcm.send_to_tokens([], title="t", body="b", data={}) == []


async def test_an_unreadable_key_file_is_swallowed(monkeypatch):
    """A bad ops configuration must not take the alert evaluator down with it."""
    monkeypatch.setattr(fcm, "get_settings", lambda: _settings(**CONFIGURED))
    dead = await fcm.send_to_tokens(["tok"], title="t", body="b", data={"url": "/alerts"})
    assert dead == []


def test_the_message_carries_both_a_notification_and_a_data_block():
    """Both are load-bearing: `notification` is what Android renders while the
    app is dead, `data` is what reaches the tap handler."""
    envelope = fcm.build_message("tok", title="Fired", body="cpu 95", data={"url": "/alerts"})
    message = envelope["message"]

    assert message["token"] == "tok"
    assert message["notification"] == {"title": "Fired", "body": "cpu 95"}
    assert message["data"] == {"url": "/alerts"}


def test_the_message_names_the_channel_the_app_creates():
    """mobile/src/lib/push.ts creates exactly this channel; Android silently
    drops a message naming one the app never created."""
    message = fcm.build_message("tok", title="t", body="b", data={})["message"]
    assert message["android"]["notification"]["channel_id"] == "alerts"
    assert message["android"]["priority"] == "high"


@pytest.mark.parametrize("status", ["UNREGISTERED", "NOT_FOUND", "INVALID_ARGUMENT"])
def test_fcm_permanent_failures_retire_the_token(status):
    assert fcm._dead_token({"error": {"status": status}}) is True


def test_a_permanent_failure_is_also_read_from_the_details_array():
    payload = {"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}}
    assert fcm._dead_token(payload) is True


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"error": {}},
        {"error": {"status": "UNAVAILABLE"}},
        {"error": {"status": "INTERNAL"}},
        {"error": {"status": "QUOTA_EXCEEDED"}},
    ],
)
def test_a_transient_failure_keeps_the_token(payload):
    """Anything not on the permanent list is retried on the next alert rather
    than deleting a device's registration over a five-minute outage."""
    assert fcm._dead_token(payload) is False


def test_a_logged_token_is_redacted():
    """A registration token is a bearer credential for delivering to that
    device — enough in the log to correlate, not enough to reuse."""
    redacted = fcm._redact("abcdefghijklmnop")
    assert redacted.startswith("abcdefgh")
    assert "ijklmnop" not in redacted
