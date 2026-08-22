import pytest

from app.config import Settings
from app.security.passwords import (
    hash_password,
    needs_rehash,
    verify_dummy_password,
    verify_password,
)


async def test_hash_is_argon2id_and_salted():
    a = await hash_password("correct horse battery staple")
    b = await hash_password("correct horse battery staple")
    assert a.startswith("$argon2id$")
    assert a != b, "identical passwords must not produce identical hashes"


async def test_verify_roundtrip():
    h = await hash_password("s3cret-passphrase")
    assert await verify_password(h, "s3cret-passphrase") is True
    assert await verify_password(h, "s3cret-passphras") is False


async def test_verify_returns_false_on_garbage_rather_than_raising():
    assert await verify_password("not-a-hash", "anything") is False
    assert await verify_password("", "anything") is False


async def test_dummy_verification_always_fails_but_does_not_raise():
    assert await verify_dummy_password("anything at all") is False


async def test_needs_rehash_flips_for_weaker_parameters():
    from argon2 import PasswordHasher, Type

    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, type=Type.ID)
    assert needs_rehash(weak.hash("x")) is True


def test_production_defaults_meet_owasp_guidance():
    """The test env deliberately weakens argon2 for speed; assert the real
    defaults separately so nobody weakens production by editing .env.test."""
    import os

    for key in list(os.environ):
        if key.startswith("ARGON2_"):
            pytest.skip("argon2 overridden in this environment")

    s = Settings(_env_file=None)
    assert s.argon2_time_cost >= 3
    assert s.argon2_memory_cost >= 65536  # 64 MiB
    assert s.argon2_hash_len >= 32
    assert s.argon2_salt_len >= 16
