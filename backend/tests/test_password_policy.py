"""The offline password screen. See app/security/password_policy.py."""

import pytest

from app.security.password_policy import password_problem


@pytest.mark.parametrize(
    "password",
    [
        "password1234",
        "PASSWORD1234",  # case folded
        "  password1234  ",  # and stripped
        "123456789012",
        "letmein123456",
        "qwertyuiop123",
        "iloveyou1234",
        "changeme1234",
    ],
)
def test_common_passwords_are_named(password):
    assert password_problem(password) == (
        "this password is one of the most commonly used and is not accepted"
    )


@pytest.mark.parametrize("password", ["aaaaaaaaaaaaaa", "abababababab", "abcabcabcabcabc"])
def test_a_repeated_motif_is_named(password):
    assert "repeated pattern" in password_problem(password)


@pytest.mark.parametrize("password", ["qwertyuiop", "0987654321", "abcdefghij", "asdfghjkl"])
def test_a_keyboard_run_is_named(password):
    assert "straight run" in password_problem(password)


@pytest.mark.parametrize(
    "password",
    [
        "a-perfectly-fine-password",
        "SecureP@ssw0rd123",
        # This project's own seeded credential, and the shape of it: a naive
        # "must not contain the email or the product name" rule rejects both,
        # which is why neither rule exists.
        "e2e-Sentinel-Test-2026",
        "correct horse battery staple",
        "xkcd-tribble-lantern-9",
    ],
)
def test_a_strong_password_has_no_problem(password):
    assert password_problem(password) is None


def test_length_is_not_this_module_s_job():
    """The Pydantic field bounds own it. Reporting it here too would give two
    different messages for one rule."""
    assert password_problem("short") is None
