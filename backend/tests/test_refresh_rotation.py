import sqlalchemy as sa

SIGNUP, REFRESH, ME = "/auth/signup", "/auth/refresh", "/auth/me"
CREDS = {"email": "rot@example.com", "password": "a-perfectly-fine-password"}


async def test_refresh_rotates_the_cookie_and_mints_a_new_access_token(client, settings):
    signup = await client.post(SIGNUP, json=CREDS)
    first_cookie = signup.cookies[settings.refresh_cookie_name]

    refreshed = await client.post(REFRESH)
    assert refreshed.status_code == 200
    second_cookie = refreshed.cookies[settings.refresh_cookie_name]

    assert second_cookie != first_cookie, "the refresh token must actually rotate"

    token = refreshed.json()["access_token"]
    me = await client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == CREDS["email"]


async def test_the_old_row_is_marked_rotated(client, admin_session):
    await client.post(SIGNUP, json=CREDS)
    await client.post(REFRESH)

    rows = (
        await admin_session.execute(
            sa.text(
                "SELECT used_at, revoked_at, revoked_reason, parent_id "
                "FROM refresh_tokens ORDER BY issued_at"
            )
        )
    ).all()
    assert len(rows) == 2
    old, new = rows
    assert old.used_at is not None
    assert old.revoked_at is not None
    assert old.revoked_reason == "rotated"
    assert new.used_at is None and new.revoked_at is None
    assert new.parent_id is not None, "the new token must record its parent"


async def test_family_id_is_stable_across_rotations(client, admin_session):
    await client.post(SIGNUP, json=CREDS)
    for _ in range(3):
        assert (await client.post(REFRESH)).status_code == 200

    families = (
        await admin_session.execute(sa.text("SELECT DISTINCT family_id FROM refresh_tokens"))
    ).all()
    assert len(families) == 1, "rotation must stay within one family"

    count = await admin_session.scalar(sa.text("SELECT count(*) FROM refresh_tokens"))
    assert count == 4  # the original plus three rotations


async def test_refresh_without_a_cookie_is_401(client):
    assert (await client.post(REFRESH)).status_code == 401


async def test_refresh_with_a_garbage_cookie_is_401(client, settings):
    client.cookies.set(settings.refresh_cookie_name, "not-a-real-token")
    assert (await client.post(REFRESH)).status_code == 401
