"""The web-push endpoint is the one URL a user chooses and the server fetches.

Before this validation existed, `endpoint` was a length-bounded string and an
authenticated user could point the server at a cloud metadata service or a
loopback port — confirmed against a running stack, four internal URLs out of
four accepted. See app/security/outbound.py.
"""

import pytest

from app.security.outbound import UnsafeUrl, resolve_is_public, validate_push_endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        # The four the assessment actually got accepted.
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:6379/",
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_INFO",
        # https, but still pointed inward.
        "https://127.0.0.1/push",
        "https://[::1]/push",
        "https://10.0.0.5/push",
        "https://192.168.1.10/push",
        "https://172.16.0.1/push",
        "https://169.254.169.254/push",
        # A single-label host is what an internal name looks like.
        "https://localhost/push",
        "https://redis/push",
        "https://metadata/push",
        # Not a URL at all.
        "not-a-url",
        "https:///push",
        "",
    ],
)
def test_unsafe_endpoints_are_refused(endpoint):
    with pytest.raises(UnsafeUrl):
        validate_push_endpoint(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://fcm.googleapis.com/fcm/send/abc123",
        "https://updates.push.services.mozilla.com/wpush/v2/gAAAA",
        "https://web.push.apple.com/QABC123",
        "https://push.example/abc",  # the fixture the other tests use
        "https://8.8.8.8/push",  # a public literal is fine
    ],
)
def test_real_push_endpoints_are_accepted(endpoint):
    assert validate_push_endpoint(endpoint) == endpoint


def test_the_error_says_which_rule_was_broken():
    """The message reaches the user as a 422 detail, so it has to be useful."""
    with pytest.raises(UnsafeUrl, match="https"):
        validate_push_endpoint("http://push.example/abc")
    with pytest.raises(UnsafeUrl, match="private or loopback"):
        validate_push_endpoint("https://127.0.0.1/abc")


def test_resolution_refuses_an_address_that_points_back_at_us():
    """The send-time half. A name that passed the syntactic check can still
    resolve inward, either by a record change or on purpose."""
    assert resolve_is_public("https://127.0.0.1/push") is False
    assert resolve_is_public("https://10.1.2.3/push") is False
    assert resolve_is_public("https://[::1]/push") is False
    # A name that does not resolve is not one we were going to reach.
    assert resolve_is_public("https://no-such-host.invalid/push") is False
    assert resolve_is_public("not-a-url") is False
