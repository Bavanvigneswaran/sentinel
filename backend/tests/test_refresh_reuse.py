"""Refresh-token replay must revoke the entire family.

This is the security property the whole rotating-refresh design exists for: if a
cookie is stolen, either the thief or the legitimate user will present an
already-exchanged token, and we cannot tell which. Killing the chain forces a
fresh login and bounds the damage.
"""

import sqlalchemy as sa

SIGNUP, LOGIN, REFRESH = "/auth/signup", "/auth/login", "/auth/refresh"
CREDS = {"email": "reuse@example.com", "password": "a-perfectly-fine-password"}


async def test_replaying_a_used_token_revokes_the_whole_family(client, settings, admin_session):
    signup = await client.post(SIGNUP, json=CREDS)
    stale = signup.cookies[settings.refresh_cookie_name]

    assert (await client.post(REFRESH)).status_code == 200  # stale is now spent

    # Replay the stale cookie, exactly as a thief with a copied cookie would.
    client.cookies.set(settings.refresh_cookie_name, stale)
    replay = await client.post(REFRESH)
    assert replay.status_code == 401

    rows = (
        await admin_session.execute(
            sa.text("SELECT revoked_at, revoked_reason FROM refresh_tokens")
        )
    ).all()
    assert all(r.revoked_at is not None for r in rows), "every token in the family must die"
    assert any(r.revoked_reason == "reuse_detected" for r in rows)


async def test_the_current_token_stops_working_after_reuse_is_detected(client, settings):
    signup = await client.post(SIGNUP, json=CREDS)
    stale = signup.cookies[settings.refresh_cookie_name]

    good = await client.post(REFRESH)
    current = good.cookies[settings.refresh_cookie_name]

    client.cookies.set(settings.refresh_cookie_name, stale)
    assert (await client.post(REFRESH)).status_code == 401

    # The legitimate, never-replayed token must also be dead.
    client.cookies.set(settings.refresh_cookie_name, current)
    assert (await client.post(REFRESH)).status_code == 401


async def test_reuse_does_not_touch_another_users_family(client, settings, admin_session):
    victim = {"email": "victim@example.com", "password": "a-perfectly-fine-password"}
    await client.post(SIGNUP, json=victim)
    victim_cookie = client.cookies[settings.refresh_cookie_name]

    client.cookies.clear()
    signup = await client.post(SIGNUP, json=CREDS)
    stale = signup.cookies[settings.refresh_cookie_name]
    await client.post(REFRESH)
    client.cookies.set(settings.refresh_cookie_name, stale)
    assert (await client.post(REFRESH)).status_code == 401

    # The bystander's session must be entirely unaffected.
    client.cookies.clear()
    client.cookies.set(settings.refresh_cookie_name, victim_cookie)
    assert (await client.post(REFRESH)).status_code == 200

    reasons = (
        await admin_session.execute(
            sa.text(
                "SELECT count(*) FROM refresh_tokens r JOIN users u ON u.id = r.user_id "
                "WHERE u.email = :e AND r.revoked_reason = 'reuse_detected'"
            ),
            {"e": victim["email"]},
        )
    ).scalar()
    assert reasons == 0


async def test_a_revoked_token_does_not_re_escalate(client, settings, admin_session):
    """Replaying an already-revoked token is a 401, but must not be recorded as
    a fresh reuse event — the family is already dead."""
    signup = await client.post(SIGNUP, json=CREDS)
    stale = signup.cookies[settings.refresh_cookie_name]
    await client.post(REFRESH)

    client.cookies.set(settings.refresh_cookie_name, stale)
    await client.post(REFRESH)  # triggers reuse

    before = await admin_session.scalar(
        sa.text("SELECT count(*) FROM refresh_tokens WHERE revoked_reason = 'reuse_detected'")
    )
    client.cookies.set(settings.refresh_cookie_name, stale)
    assert (await client.post(REFRESH)).status_code == 401
    after = await admin_session.scalar(
        sa.text("SELECT count(*) FROM refresh_tokens WHERE revoked_reason = 'reuse_detected'")
    )
    assert before == after
