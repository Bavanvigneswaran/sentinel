import sqlalchemy as sa

SIGNUP = "/auth/signup"
GOOD = {"email": "new@example.com", "password": "a-perfectly-fine-password", "display_name": "New"}


async def test_signup_returns_201_with_a_session(client):
    r = await client.post(SIGNUP, json=GOOD)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["email"] == "new@example.com"
    assert body["user"]["display_name"] == "New"


async def test_signup_sets_the_refresh_cookie(client, settings):
    r = await client.post(SIGNUP, json=GOOD)
    assert settings.refresh_cookie_name in r.cookies


async def test_signup_response_never_contains_the_password_or_its_hash(client):
    r = await client.post(SIGNUP, json=GOOD)
    raw = r.text
    assert "password_hash" not in raw
    assert "argon2" not in raw
    assert GOOD["password"] not in raw


async def test_duplicate_email_returns_409(client):
    assert (await client.post(SIGNUP, json=GOOD)).status_code == 201
    r = await client.post(SIGNUP, json=GOOD)
    assert r.status_code == 409
    assert r.json()["detail"] == "Email already registered"


async def test_email_is_normalized_so_case_variants_collide(client):
    assert (await client.post(SIGNUP, json={**GOOD, "email": "Foo@Example.COM"})).status_code == 201
    r = await client.post(SIGNUP, json={**GOOD, "email": "foo@example.com"})
    assert r.status_code == 409


async def test_short_password_is_rejected(client, settings):
    short = "x" * (settings.password_min_length - 1)
    r = await client.post(SIGNUP, json={**GOOD, "password": short})
    assert r.status_code == 422


async def test_absurdly_long_password_is_rejected(client, settings):
    r = await client.post(SIGNUP, json={**GOOD, "password": "x" * 100_000})
    assert r.status_code == 422, "an unbounded password field is a CPU-amplification vector"


async def test_malformed_email_is_rejected(client):
    r = await client.post(SIGNUP, json={**GOOD, "email": "not-an-email"})
    assert r.status_code == 422


async def test_password_is_stored_as_an_argon2id_hash(client, admin_session):
    await client.post(SIGNUP, json=GOOD)
    stored = await admin_session.scalar(
        sa.text("SELECT password_hash FROM users WHERE email = 'new@example.com'")
    )
    assert stored.startswith("$argon2id$")
    assert GOOD["password"] not in stored
