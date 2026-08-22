import uuid
from datetime import UTC, datetime, timedelta

import jwt
import sqlalchemy as sa

from app.config import get_settings

SIGNUP, ME = "/auth/signup", "/auth/me"
CREDS = {"email": "me@example.com", "password": "a-perfectly-fine-password"}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_me_returns_the_current_user(client):
    token = (await client.post(SIGNUP, json=CREDS)).json()["access_token"]
    r = await client.get(ME, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == CREDS["email"]
    assert "password_hash" not in body


async def test_me_without_a_token_is_401(client):
    assert (await client.get(ME)).status_code == 401


async def test_me_with_a_tampered_token_is_401(client):
    token = (await client.post(SIGNUP, json=CREDS)).json()["access_token"]
    head, body, sig = token.split(".")
    assert (await client.get(ME, headers=_auth(f"{head}.{body}.{sig[:-4]}AAAA"))).status_code == 401


async def test_me_with_an_expired_token_is_401(client):
    s = get_settings()
    await client.post(SIGNUP, json=CREDS)
    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iat": now - timedelta(hours=2),
            "nbf": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
            "jti": str(uuid.uuid4()),
            "typ": "access",
            "iss": s.jwt_issuer,
            "aud": s.jwt_audience,
        },
        s.jwt_secret,
        algorithm=s.jwt_algorithm,
    )
    assert (await client.get(ME, headers=_auth(expired))).status_code == 401


async def test_a_valid_token_for_a_deleted_user_is_401(client, admin_session):
    token = (await client.post(SIGNUP, json=CREDS)).json()["access_token"]
    await admin_session.execute(
        sa.text("DELETE FROM users WHERE email = :e"), {"e": CREDS["email"]}
    )
    await admin_session.commit()
    assert (await client.get(ME, headers=_auth(token))).status_code == 401


async def test_deactivating_a_user_mid_session_is_401(client, admin_session):
    token = (await client.post(SIGNUP, json=CREDS)).json()["access_token"]
    await admin_session.execute(
        sa.text("UPDATE users SET is_active = false WHERE email = :e"), {"e": CREDS["email"]}
    )
    await admin_session.commit()
    assert (await client.get(ME, headers=_auth(token))).status_code == 401


async def test_a_password_change_invalidates_outstanding_access_tokens(client, admin_session):
    """password_changed_at is compared against the token's iat, so a password
    change logs out existing sessions with no denylist."""
    token = (await client.post(SIGNUP, json=CREDS)).json()["access_token"]
    assert (await client.get(ME, headers=_auth(token))).status_code == 200

    await admin_session.execute(
        sa.text(
            "UPDATE users SET password_changed_at = now() + interval '1 hour' WHERE email = :e"
        ),
        {"e": CREDS["email"]},
    )
    await admin_session.commit()
    assert (await client.get(ME, headers=_auth(token))).status_code == 401
