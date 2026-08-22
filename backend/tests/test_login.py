import sqlalchemy as sa

SIGNUP, LOGIN = "/auth/signup", "/auth/login"
CREDS = {"email": "user@example.com", "password": "a-perfectly-fine-password"}


async def _register(client):
    return await client.post(SIGNUP, json=CREDS)


async def test_login_succeeds(client):
    await _register(client)
    r = await client.post(LOGIN, json=CREDS)
    assert r.status_code == 200
    assert r.json()["user"]["email"] == CREDS["email"]


async def test_wrong_password_is_401(client):
    await _register(client)
    r = await client.post(LOGIN, json={**CREDS, "password": "wrong-but-long-enough"})
    assert r.status_code == 401


async def test_unknown_email_is_indistinguishable_from_a_wrong_password(client):
    """Same status and same body, so login is not a user-enumeration oracle."""
    await _register(client)
    wrong_pw = await client.post(LOGIN, json={**CREDS, "password": "wrong-but-long-enough"})
    unknown = await client.post(
        LOGIN, json={"email": "nobody@example.com", "password": "wrong-but-long-enough"}
    )
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()


async def test_deactivated_account_cannot_log_in(client, admin_session):
    await _register(client)
    await admin_session.execute(
        sa.text("UPDATE users SET is_active = false WHERE email = :e"), {"e": CREDS["email"]}
    )
    await admin_session.commit()
    r = await client.post(LOGIN, json=CREDS)
    assert r.status_code == 401


async def test_login_updates_last_login_at(client, admin_session):
    await _register(client)
    await client.post(LOGIN, json=CREDS)
    value = await admin_session.scalar(
        sa.text("SELECT last_login_at FROM users WHERE email = :e"), {"e": CREDS["email"]}
    )
    assert value is not None


async def test_login_is_case_insensitive_on_email(client):
    await _register(client)
    r = await client.post(LOGIN, json={**CREDS, "email": "USER@Example.com"})
    assert r.status_code == 200
