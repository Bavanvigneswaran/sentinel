from unittest.mock import patch

import pytest

from app.api.ratelimit import RateLimit

LOGIN = "/auth/login"
SIGNUP = "/auth/signup"
CREDS = {"email": "rl@example.com", "password": "a-perfectly-fine-password"}


async def test_repeated_failed_logins_are_throttled(client, settings, redis_client):
    await client.post(SIGNUP, json=CREDS)
    await redis_client.flushdb()

    bad = {**CREDS, "password": "wrong-but-long-enough"}
    statuses = [
        (await client.post(LOGIN, json=bad)).status_code
        for _ in range(settings.rl_login_per_minute + 2)
    ]
    assert 429 in statuses, "brute-force protection is not engaging"


async def test_a_429_carries_a_numeric_retry_after(client, settings, redis_client):
    await client.post(SIGNUP, json=CREDS)
    await redis_client.flushdb()

    bad = {**CREDS, "password": "wrong-but-long-enough"}
    for _ in range(settings.rl_login_per_minute + 3):
        r = await client.post(LOGIN, json=bad)
        if r.status_code == 429:
            assert int(r.headers["Retry-After"]) > 0
            assert r.headers["X-RateLimit-Remaining"] == "0"
            return
    pytest.fail("never hit the limit")


async def test_the_limiter_fails_open_when_redis_is_unreachable(client, redis_client):
    """A Redis outage must not lock every user out of the product."""
    await client.post(SIGNUP, json=CREDS)
    await redis_client.flushdb()

    with patch("app.api.ratelimit._hit", side_effect=ConnectionError("redis is down")):
        r = await client.post(LOGIN, json=CREDS)
    assert r.status_code == 200


async def test_the_limiter_is_a_noop_when_disabled(client, settings, monkeypatch, redis_client):
    limiter = RateLimit("t", limit=1, window=60, key="ip")
    monkeypatch.setattr(settings, "rate_limit_enabled", False)

    class _Req:
        client = type("C", (), {"host": "1.2.3.4"})()

    for _ in range(5):
        await limiter(_Req())  # must not raise


async def test_emails_are_not_stored_in_plaintext_in_redis(client, redis_client):
    await client.post(SIGNUP, json=CREDS)
    bad = {**CREDS, "password": "wrong-but-long-enough"}
    await client.post(LOGIN, json=bad)

    keys = [k async for k in redis_client.scan_iter("rl:*")]
    assert keys, "no rate limit keys were written"
    assert not any(CREDS["email"] in k for k in keys)
