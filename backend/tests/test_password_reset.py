"""The forgot/reset password flow.

Two properties carry most of the weight here and are asserted from several
angles: the request endpoint must not reveal whether an address has an account,
and spending a link must invalidate everything the old password could still
reach.
"""

from urllib.parse import parse_qs, urlparse

import pytest

from app.security.passwords import hash_password, verify_password

FORGOT = "/auth/forgot-password"
RESET = "/auth/reset-password"
LOGIN = "/auth/login"
REFRESH = "/auth/refresh"

OLD_PASSWORD = "the-original-password"
NEW_PASSWORD = "a-completely-different-one"


@pytest.fixture
def sent(monkeypatch):
    """Capture reset emails instead of sending them.

    Patched on the module rather than on the route, because the route hands
    `background.add_task` the module attribute and resolves it when it runs.
    """
    captured: list[tuple[str, str]] = []

    async def _capture(to_address: str, reset_url: str) -> None:
        captured.append((to_address, reset_url))

    monkeypatch.setattr("app.services.password_reset.send_reset_email", _capture)
    return captured


def token_from(reset_url: str) -> str:
    return parse_qs(urlparse(reset_url).query)["token"][0]


@pytest.fixture
async def user(make_user):
    return await make_user(
        email="reset-me@example.com", password_hash=await hash_password(OLD_PASSWORD)
    )


async def request_link(client, sent, email: str = "reset-me@example.com") -> str:
    r = await client.post(FORGOT, json={"email": email})
    assert r.status_code == 202
    return token_from(sent[-1][1])


# --- enumeration ----------------------------------------------------------


async def test_an_unknown_address_is_answered_exactly_like_a_known_one(client, user, sent):
    """The status code is the whole contract: anything that differed here
    would turn this endpoint into an account-enumeration oracle."""
    known = await client.post(FORGOT, json={"email": user.email})
    unknown = await client.post(FORGOT, json={"email": "nobody@example.com"})

    assert known.status_code == unknown.status_code == 202
    assert known.text == unknown.text


async def test_an_unknown_address_is_not_mailed(client, sent):
    await client.post(FORGOT, json={"email": "nobody@example.com"})
    assert sent == []


async def test_a_deactivated_account_gets_no_link(client, make_user, sent):
    await make_user(
        email="gone@example.com", password_hash=await hash_password(OLD_PASSWORD), is_active=False
    )
    r = await client.post(FORGOT, json={"email": "gone@example.com"})

    assert r.status_code == 202, "still indistinguishable from an active account"
    assert sent == []


async def test_the_address_is_matched_case_insensitively(client, user, sent):
    """Emails are stored normalized, so a capitalised request must still find
    the account rather than silently looking like an unknown address."""
    await client.post(FORGOT, json={"email": "Reset-Me@Example.com"})
    assert len(sent) == 1


# --- the round trip -------------------------------------------------------


async def test_a_link_sets_the_new_password_and_signs_the_user_in(client, user, sent):
    token = await request_link(client, sent)

    r = await client.post(RESET, json={"token": token, "password": NEW_PASSWORD})

    assert r.status_code == 200
    assert r.json()["user"]["email"] == user.email
    assert r.json()["access_token"]

    after = await client.post(LOGIN, json={"email": user.email, "password": NEW_PASSWORD})
    assert after.status_code == 200


async def test_the_old_password_stops_working(client, user, sent):
    token = await request_link(client, sent)
    await client.post(RESET, json={"token": token, "password": NEW_PASSWORD})

    r = await client.post(LOGIN, json={"email": user.email, "password": OLD_PASSWORD})
    assert r.status_code == 401


async def test_the_stored_hash_is_argon2_over_the_new_password(client, user, sent, admin_session):
    token = await request_link(client, sent)
    await client.post(RESET, json={"token": token, "password": NEW_PASSWORD})

    await admin_session.refresh(user)
    assert user.password_hash.startswith("$argon2")
    assert await verify_password(user.password_hash, NEW_PASSWORD)


async def test_the_link_points_at_the_reset_page(client, user, sent):
    await client.post(FORGOT, json={"email": user.email})
    _, url = sent[-1]
    assert urlparse(url).path == "/reset-password"


async def test_the_link_goes_to_the_accounts_own_address(client, user, sent):
    await client.post(FORGOT, json={"email": user.email})
    assert sent[-1][0] == user.email


# --- the token is a credential --------------------------------------------


async def test_a_link_works_only_once(client, user, sent):
    token = await request_link(client, sent)
    first = await client.post(RESET, json={"token": token, "password": NEW_PASSWORD})
    second = await client.post(RESET, json={"token": token, "password": "yet-another-password"})

    assert first.status_code == 200
    assert second.status_code == 400


@pytest.mark.parametrize("token", ["", "not-a-token", "x" * 64])
async def test_a_token_that_was_never_issued_is_refused(client, user, token):
    r = await client.post(RESET, json={"token": token, "password": NEW_PASSWORD})
    assert r.status_code in (400, 422)


async def test_the_raw_token_is_not_what_is_stored(client, user, sent, redis_client):
    """Only a sha256 of the token lives in Redis, so a dump of the store is
    not a list of working reset links."""
    token = await request_link(client, sent)

    keys = [k async for k in redis_client.scan_iter(match="auth:pwreset:*")]
    assert keys, "the token should have been stored"
    values = [await redis_client.get(k) for k in keys]

    assert all(token not in k for k in keys)
    assert all(token not in v for v in values)


async def test_using_a_link_invalidates_the_others(client, user, sent):
    """Two links requested minutes apart must not each work: honouring the
    older one after the newer would silently undo the newer reset."""
    older = await request_link(client, sent)
    newer = await request_link(client, sent)

    used = await client.post(RESET, json={"token": newer, "password": NEW_PASSWORD})
    stale = await client.post(RESET, json={"token": older, "password": "a-third-password"})

    assert used.status_code == 200
    assert stale.status_code == 400


# --- what a reset must invalidate -----------------------------------------


async def test_a_reset_revokes_every_existing_session(client, user, sent):
    """A thief holding a live session must lose it here — that is the point of
    a reset, and the half a new password alone does not achieve."""
    await client.post(LOGIN, json={"email": user.email, "password": OLD_PASSWORD})
    assert (await client.post(REFRESH)).status_code == 200, "session is live before the reset"

    token = await request_link(client, sent)
    await client.post(RESET, json={"token": token, "password": NEW_PASSWORD})

    # "rotated" is also present, from the refresh above — that one was spent
    # normally. What matters is that the live token it produced was revoked by
    # the reset rather than left usable.
    assert "password_change" in await _revocations(user.id)


async def _revocations(user_id):
    import sqlalchemy as sa

    from app.db import AdminSessionLocal
    from app.models import RefreshToken

    async with AdminSessionLocal() as s:
        return list(
            await s.scalars(
                sa.select(RefreshToken.revoked_reason).where(
                    RefreshToken.user_id == user_id, RefreshToken.revoked_reason.is_not(None)
                )
            )
        )


async def test_a_reset_bumps_password_changed_at(client, user, sent, admin_session):
    """Which is what rejects access tokens issued before it — an access JWT is
    valid on its signature alone and is checked against no table."""
    before = user.password_changed_at
    token = await request_link(client, sent)
    await client.post(RESET, json={"token": token, "password": NEW_PASSWORD})

    await admin_session.refresh(user)
    assert user.password_changed_at > before


# --- the new password is held to the signup policy ------------------------


@pytest.mark.parametrize("weak", ["short", "password123456", "aaaaaaaaaaaaaaa"])
async def test_a_weak_new_password_is_refused(client, user, sent, weak):
    token = await request_link(client, sent)
    r = await client.post(RESET, json={"token": token, "password": weak})
    assert r.status_code == 422


async def test_a_refused_password_does_not_burn_the_link(client, user, sent):
    """The policy runs in the Pydantic validator, before the handler — so a
    weak choice is a retype, not a dead link and a second email."""
    token = await request_link(client, sent)
    await client.post(RESET, json={"token": token, "password": "short"})

    retry = await client.post(RESET, json={"token": token, "password": NEW_PASSWORD})
    assert retry.status_code == 200
