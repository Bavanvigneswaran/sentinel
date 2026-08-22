"""Opaque secrets: refresh tokens, agent tokens, and enrollment codes.

All three are high-entropy random values stored as sha256 at rest. sha256 rather
than argon2 is deliberate: argon2 exists to make *low-entropy* secrets expensive
to guess, and these are 256- and 60-bit random values that are not guessable at
any hash speed.

There is no constant-time comparison here on purpose — verification is always a
lookup on the unique index over the hash column, never a Python-side compare.
"""

from __future__ import annotations

import hashlib
import secrets

AGENT_TOKEN_SCHEME = "sag_"  # noqa: S105 — a scheme prefix, not a credential
AGENT_TOKEN_PREFIX_LEN = 12

# Crockford base32: no I, L, O or U, so nothing is ambiguous when a user reads a
# code off the screen and types it into an agent prompt.
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ENROLLMENT_CODE_GROUPS = 3
ENROLLMENT_CODE_GROUP_LEN = 4  # 12 chars total = 60 bits


def sha256_bytes(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def new_secret() -> str:
    """256 bits, URL-safe."""
    return secrets.token_urlsafe(32)


def new_refresh_token() -> tuple[str, bytes]:
    """Returns (secret, hash). Only the secret goes in the cookie."""
    secret = new_secret()
    return secret, sha256_bytes(secret)


def new_agent_token() -> tuple[str, str, bytes]:
    """Returns (token, prefix, hash).

    The prefix is shown in the UI so a user can tell two tokens apart; it is not
    secret and is far too short to authenticate with.
    """
    token = AGENT_TOKEN_SCHEME + new_secret()
    return token, token[:AGENT_TOKEN_PREFIX_LEN], sha256_bytes(token)


def new_enrollment_code() -> tuple[str, str, bytes]:
    """Returns (display_code, prefix, hash), e.g. ('X4T9-K2QM-7PDR', 'X4T9', ...)."""
    groups = [
        "".join(
            secrets.choice(CROCKFORD_ALPHABET) for _ in range(ENROLLMENT_CODE_GROUP_LEN)
        )
        for _ in range(ENROLLMENT_CODE_GROUPS)
    ]
    display = "-".join(groups)
    return display, groups[0], sha256_bytes(normalize_code(display))


def normalize_code(code: str) -> str:
    """Uppercase and strip separators, so formatting never affects the hash."""
    return "".join(c for c in code.upper() if c.isalnum())
