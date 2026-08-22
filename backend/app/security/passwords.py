"""Argon2id password hashing.

Every entry point is async and offloads to the threadpool. Argon2 at production
parameters costs 60-100ms of pure CPU per call; running it inline would block
the event loop (a hard rule in CLAUDE.md) and make login a trivial DoS vector.
argon2-cffi releases the GIL inside its C hashing loop, so the threadpool gives
real parallelism rather than merely non-blocking behaviour.
"""

from __future__ import annotations

from argon2 import PasswordHasher, Type
from argon2.exceptions import (
    HashingError,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from starlette.concurrency import run_in_threadpool

from app.config import get_settings

_settings = get_settings()

# Parameters are pinned in Settings rather than left to library defaults, so a
# dependency upgrade cannot silently weaken them.
_hasher = PasswordHasher(
    time_cost=_settings.argon2_time_cost,
    memory_cost=_settings.argon2_memory_cost,
    parallelism=_settings.argon2_parallelism,
    hash_len=_settings.argon2_hash_len,
    salt_len=_settings.argon2_salt_len,
    type=Type.ID,
)

_dummy_hash: str | None = None


def _get_dummy_hash() -> str:
    """A real hash to verify against when the email is unknown.

    Without this, an unknown email returns measurably faster than a wrong
    password and the login endpoint becomes a user-enumeration oracle.
    """
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _hasher.hash("dummy-password-for-constant-time-comparison")
    return _dummy_hash


async def hash_password(password: str) -> str:
    return await run_in_threadpool(_hasher.hash, password)


def _verify(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, HashingError):
        return False


async def verify_password(stored_hash: str, password: str) -> bool:
    """Verify a password. Returns False rather than raising, for any failure."""
    return await run_in_threadpool(_verify, stored_hash, password)


async def verify_dummy_password(password: str) -> bool:
    """Burn an equivalent amount of CPU on the unknown-email path."""
    return await run_in_threadpool(_verify, _get_dummy_hash(), password)


def needs_rehash(stored_hash: str) -> bool:
    """True when the hash was made with weaker parameters than we now use."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True
