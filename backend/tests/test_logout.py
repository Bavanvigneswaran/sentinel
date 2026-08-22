import sqlalchemy as sa

SIGNUP, LOGIN, LOGOUT, REFRESH = "/auth/signup", "/auth/login", "/auth/logout", "/auth/refresh"
CREDS = {"email": "out@example.com", "password": "a-perfectly-fine-password"}


async def test_logout_is_204_and_clears_the_cookie(client, settings):
    await client.post(SIGNUP, json=CREDS)
    r = await client.post(LOGOUT)
    assert r.status_code == 204

    header = next(
        h for h in r.headers.get_list("set-cookie") if h.startswith(settings.refresh_cookie_name)
    )
    assert "Max-Age=0" in header or 'expires=Thu, 01 Jan 1970' in header.lower()


async def test_logout_revokes_the_whole_family(client, admin_session):
    await client.post(SIGNUP, json=CREDS)
    await client.post(REFRESH)
    await client.post(LOGOUT)

    rows = (
        await admin_session.execute(
            sa.text("SELECT revoked_at, revoked_reason FROM refresh_tokens")
        )
    ).all()
    assert all(r.revoked_at is not None for r in rows)
    assert any(r.revoked_reason == "logout" for r in rows)


async def test_refresh_after_logout_is_401(client, settings):
    signup = await client.post(SIGNUP, json=CREDS)
    cookie = signup.cookies[settings.refresh_cookie_name]
    await client.post(LOGOUT)

    client.cookies.set(settings.refresh_cookie_name, cookie)
    assert (await client.post(REFRESH)).status_code == 401


async def test_logout_without_a_cookie_still_succeeds(client):
    """A logout that can fail is a bug."""
    assert (await client.post(LOGOUT)).status_code == 204


async def test_logout_with_an_unknown_cookie_still_succeeds(client, settings):
    client.cookies.set(settings.refresh_cookie_name, "never-issued")
    assert (await client.post(LOGOUT)).status_code == 204


async def test_login_after_logout_works(client):
    await client.post(SIGNUP, json=CREDS)
    await client.post(LOGOUT)
    assert (await client.post(LOGIN, json=CREDS)).status_code == 200
