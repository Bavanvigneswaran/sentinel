"""Screening a chosen password against the ones attackers try first.

The length floor (12–128, `Settings.password_min_length`) follows NIST SP
800-63B, which explicitly discourages composition rules — no "one uppercase,
one digit, one symbol". But 800-63B pairs that floor with a check against known
compromised passwords, and only the first half was implemented: `password123456`
is fourteen characters and was accepted.

This is the second half, done offline. A Have I Been Pwned range lookup would
cover far more, and is deliberately not used: it would put a network call in the
signup path of a product that otherwise calls nothing external, and a signup
that fails because a third party is down is a worse outcome than one that misses
an uncommon breached password.

What it catches instead is the shape of a guess rather than a specific string:
the handful of long passwords that top every breach corpus, anything that is one
character or one short motif repeated, and any run along a keyboard row or the
digits. Those are what an attacker with a 12-character floor actually tries.

Deliberately *not* checked: whether the password contains the email address or
the product name. Both sound sensible and both would be wrong here — this
project's own end-to-end account is seeded as `e2e@example.com` with
`e2e-Sentinel-Test-2026`, which a naive version of either rule rejects. A policy
that fails a password a human would call strong is a policy people route around.
"""

from __future__ import annotations

#: Passwords of 12+ characters that appear at the top of every public breach
#: corpus. Short entries are pointless — the length floor already refuses them.
COMMON_PASSWORDS = frozenset(
    {
        "123456789012",
        "1234567890123",
        "12345678901234",
        "123456789012345",
        "1234567890abc",
        "123123123123",
        "112233445566",
        "000000000000",
        "111111111111",
        "aaaaaaaaaaaa",
        "abcdefghijkl",
        "abcd1234abcd",
        "password1234",
        "password12345",
        "password123456",
        "passw0rd1234",
        "mypassword123",
        "letmein123456",
        "qwertyuiop123",
        "qwertyuiop12",
        "qwerty123456",
        "qwerty1234567",
        "1qaz2wsx3edc",
        "zaq12wsxcde3",
        "iloveyou1234",
        "iloveyou12345",
        "welcome123456",
        "welcome1234",
        "trustno1234567",
        "administrator",
        "administrator1",
        "sunshine12345",
        "princess12345",
        "football12345",
        "baseball12345",
        "superman12345",
        "michael123456",
        "jennifer12345",
        "dragon123456",
        "monkey123456",
        "shadow123456",
        "master123456",
        "changeme1234",
        "changeit1234",
        "secretpassword",
        "thisisapassword",
        "correcthorsebatterystaple",
    }
)

#: Runs a guess walks along. Checked in both directions, so `0987654321` and
#: `poiuytrewq` are caught by the same entries.
_RUNS = (
    "01234567890123456789",
    "abcdefghijklmnopqrstuvwxyz",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
    "qwertyuiopasdfghjklzxcvbnm",
)

#: The longest motif worth testing for repetition. Beyond this a repeat is a
#: passphrase pattern rather than a keyboard mash — "correct-horse-correct-horse"
#: is not what this is for.
_MAX_MOTIF = 4


def _is_repeated_motif(password: str) -> bool:
    """True when the whole password is one short motif repeated."""
    for size in range(1, _MAX_MOTIF + 1):
        if len(password) <= size:
            break
        motif = password[:size]
        if (motif * (len(password) // size + 1))[: len(password)] == password:
            return True
    return False


def _is_a_run(password: str) -> bool:
    """True when the password is a straight slice of a keyboard or digit run."""
    for run in _RUNS:
        if password in run or password in run[::-1]:
            return True
    return False


def password_problem(password: str) -> str | None:
    """The reason this password is unacceptable, or None.

    Length is not checked here — the Pydantic field bounds own that, and
    reporting it twice would give two different messages for one rule.
    """
    folded = password.strip().lower()
    if not folded:
        return None

    if folded in COMMON_PASSWORDS:
        return "this password is one of the most commonly used and is not accepted"
    if _is_repeated_motif(folded):
        return "this password is a single repeated pattern and is not accepted"
    if _is_a_run(folded):
        return "this password is a straight run of keys and is not accepted"
    return None


__all__ = ["COMMON_PASSWORDS", "password_problem"]
