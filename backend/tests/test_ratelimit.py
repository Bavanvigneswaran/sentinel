from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.api.ratelimit import RateLimit, reset_local_limiter
from app.config import Settings

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
    reset_local_limiter()
    await client.post(SIGNUP, json=CREDS)
    await redis_client.flushdb()

    with patch("app.api.ratelimit._hit", side_effect=ConnectionError("redis is down")):
        r = await client.post(LOGIN, json=CREDS)
    assert r.status_code == 200


async def test_a_redis_outage_degrades_the_limiter_rather_than_removing_it(
    client, settings, redis_client
):
    """Failing open is the right trade; failing open all the way to *unlimited*
    was not.

    Brute-force protection on /auth/login and on /enroll — the only
    unauthenticated write in the system — used to disappear entirely for the
    length of an outage, with one ERROR log line as the only sign. The
    in-process fallback is weaker than Redis (per-worker, and forgotten on
    restart) but it is a limit."""
    reset_local_limiter()
    await client.post(SIGNUP, json=CREDS)
    await redis_client.flushdb()

    bad = {**CREDS, "password": "wrong-but-long-enough"}
    with patch("app.api.ratelimit._hit", side_effect=ConnectionError("redis is down")):
        statuses = [
            (await client.post(LOGIN, json=bad)).status_code
            for _ in range(settings.rl_login_per_minute + 4)
        ]

    assert 401 in statuses, "the first attempts should still be served"
    assert 429 in statuses, "the fallback never engaged — the endpoint is unthrottled"
    reset_local_limiter()


async def test_the_fallback_forgets_its_state_when_reset(client, redis_client):
    """The counter is per-process and does not survive a restart. Asserted so
    that limitation is a stated property rather than a surprise."""
    reset_local_limiter()
    await client.post(SIGNUP, json=CREDS)
    await redis_client.flushdb()

    limiter = RateLimit("fallback-probe", limit=1, window=60, key="ip")

    class _Req:
        client = type("C", (), {"host": "203.0.113.7"})()

    with patch("app.api.ratelimit._hit", side_effect=ConnectionError("redis is down")):
        await limiter(_Req())
        with pytest.raises(HTTPException) as refused:
            await limiter(_Req())
        assert refused.value.status_code == 429

        reset_local_limiter()
        await limiter(_Req())  # a fresh process would allow it again
    reset_local_limiter()


async def test_the_limiter_is_a_noop_when_disabled(monkeypatch):
    """Patch the module's settings lookup rather than mutating a shared object,
    so this does not depend on the state of the lru_cached instance."""
    import app.api.ratelimit as rl

    disabled = Settings(_env_file=None, environment="dev", rate_limit_enabled=False)
    monkeypatch.setattr(rl, "get_settings", lambda: disabled)

    limiter = RateLimit("t", limit=1, window=60, key="ip")

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
