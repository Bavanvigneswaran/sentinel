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


# --- the other half of NIST SP 800-63B --------------------------------------


async def test_signup_refuses_a_commonly_guessed_password(client):
    """The length floor follows 800-63B, which discourages composition rules —
    but pairs the floor with a check against known compromised passwords, and
    only the first half existed. `password123456` is fourteen characters."""
    for weak in (
        "password123456",
        "123456789012",
        "qwertyuiop123",
        "aaaaaaaaaaaaaa",
        "abababababababab",
    ):
        r = await client.post(SIGNUP, json={**GOOD, "password": weak})
        assert r.status_code == 422, f"{weak!r} was accepted"


async def test_the_refusal_explains_itself(client):
    r = await client.post(SIGNUP, json={**GOOD, "password": "password1234"})
    assert r.status_code == 422
    assert "commonly used" in r.text


async def test_a_long_uncommon_password_is_still_accepted(client):
    """The screening must not fail a password a human would call strong — a
    policy that does is one people route around. These include this project's
    own seeded end-to-end credential, which a naive "must not contain the
    product name or the email" rule would have rejected."""
    for strong in (
        "a-perfectly-fine-password",
        "e2e-Sentinel-Test-2026",
        "correct horse battery staple",
        "xkcd-tribble-lantern-9",
    ):
        r = await client.post(
            SIGNUP,
            json={"email": f"strong-{strong[:4].strip()}@example.com", "password": strong},
        )
        assert r.status_code == 201, f"{strong!r} was refused: {r.text}"


async def test_login_never_applies_the_policy(client):
    """Enforcing it at login would leak the policy and let an attacker skip
    candidate passwords — the same reason LoginRequest uses min_length=1."""
    r = await client.post("/auth/login", json={"email": GOOD["email"], "password": "password1234"})
    assert r.status_code == 401  # rejected as wrong, never as weak
    assert "commonly used" not in r.text
