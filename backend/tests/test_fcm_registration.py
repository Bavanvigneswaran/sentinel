"""POST/DELETE /notifications/fcm/register, and the tenancy of the token rows.

The Phase 10a Android viewer is the only caller. Registration is the opt-in —
there is no `fcm_enabled` settings flag — so these tests also pin down that a
token row alone is enough to make notify.py dispatch.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.db import AdminSessionLocal
from app.models import FcmToken

REGISTER = "/notifications/fcm/register"
SIGNUP = "/auth/signup"

TOKEN_A = "fcm-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TOKEN_B = "fcm-token-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


async def _auth_headers(client, email: str) -> dict:
    resp = await client.post(SIGNUP, json={"email": email, "password": "a-perfectly-fine-password"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _rows() -> list[FcmToken]:
    async with AdminSessionLocal() as session:
        return list(await session.scalars(sa.select(FcmToken).order_by(FcmToken.token)))


async def test_registration_requires_authentication(client):
    resp = await client.post(REGISTER, json={"token": TOKEN_A})
    assert resp.status_code == 401


async def test_register_stores_the_token_against_the_caller(client):
    headers = await _auth_headers(client, "fcm-one@example.com")

    resp = await client.post(
        REGISTER, json={"token": TOKEN_A, "device_label": "Pixel 8"}, headers=headers
    )
    assert resp.status_code == 204

    rows = await _rows()
    assert len(rows) == 1
    assert rows[0].token == TOKEN_A
    assert rows[0].device_label == "Pixel 8"


async def test_re_registering_the_same_token_upserts_rather_than_duplicating(client):
    """FCM hands a device the same token across app launches, and the app
    re-registers on every Settings visit. That must not accumulate rows."""
    headers = await _auth_headers(client, "fcm-two@example.com")

    await client.post(REGISTER, json={"token": TOKEN_A, "device_label": "old"}, headers=headers)
    await client.post(REGISTER, json={"token": TOKEN_A, "device_label": "new"}, headers=headers)

    rows = await _rows()
    assert len(rows) == 1
    assert rows[0].device_label == "new"


async def test_one_user_can_register_several_devices(client):
    headers = await _auth_headers(client, "fcm-three@example.com")

    await client.post(REGISTER, json={"token": TOKEN_A}, headers=headers)
    await client.post(REGISTER, json={"token": TOKEN_B}, headers=headers)

    assert len(await _rows()) == 2


async def test_unregister_removes_only_that_token(client):
    headers = await _auth_headers(client, "fcm-four@example.com")
    await client.post(REGISTER, json={"token": TOKEN_A}, headers=headers)
    await client.post(REGISTER, json={"token": TOKEN_B}, headers=headers)

    resp = await client.request("DELETE", REGISTER, json={"token": TOKEN_A}, headers=headers)
    assert resp.status_code == 204

    rows = await _rows()
    assert [r.token for r in rows] == [TOKEN_B]


async def test_unregistering_an_unknown_token_is_a_no_op(client):
    headers = await _auth_headers(client, "fcm-five@example.com")
    resp = await client.request("DELETE", REGISTER, json={"token": TOKEN_A}, headers=headers)
    assert resp.status_code == 204


async def test_a_user_cannot_delete_another_users_token(client):
    """RLS, not just the belt-and-braces user_id filter in the route."""
    owner = await _auth_headers(client, "fcm-owner@example.com")
    intruder = await _auth_headers(client, "fcm-intruder@example.com")

    await client.post(REGISTER, json={"token": TOKEN_A}, headers=owner)
    await client.request("DELETE", REGISTER, json={"token": TOKEN_A}, headers=intruder)

    assert [r.token for r in await _rows()] == [TOKEN_A]


async def test_a_phone_already_registered_to_another_account_gets_a_409(client):
    """One physical phone, two Sentinel accounts. RLS makes the existing row
    invisible, so the upsert cannot rewrite it and Postgres refuses the
    statement — the route turns that into an actionable conflict rather than a
    500, and leaves the first account's registration untouched."""
    first = await _auth_headers(client, "fcm-phone-first@example.com")
    second = await _auth_headers(client, "fcm-phone-second@example.com")

    await client.post(REGISTER, json={"token": TOKEN_A, "device_label": "first"}, headers=first)
    resp = await client.post(
        REGISTER, json={"token": TOKEN_A, "device_label": "second"}, headers=second
    )

    assert resp.status_code == 409
    assert "different Sentinel account" in resp.json()["detail"]

    rows = await _rows()
    assert len(rows) == 1
    assert rows[0].device_label == "first"


async def test_the_first_account_can_hand_the_phone_over_by_unregistering(client):
    """The path the app takes on sign-out, and the reason the 409 above is a
    dead end nobody should normally hit."""
    first = await _auth_headers(client, "fcm-handover-first@example.com")
    second = await _auth_headers(client, "fcm-handover-second@example.com")

    await client.post(REGISTER, json={"token": TOKEN_A}, headers=first)
    await client.request("DELETE", REGISTER, json={"token": TOKEN_A}, headers=first)

    resp = await client.post(
        REGISTER, json={"token": TOKEN_A, "device_label": "second"}, headers=second
    )
    assert resp.status_code == 204

    rows = await _rows()
    assert len(rows) == 1
    assert rows[0].device_label == "second"


async def test_the_token_column_rejects_an_unbounded_string(client):
    headers = await _auth_headers(client, "fcm-six@example.com")
    resp = await client.post(REGISTER, json={"token": "x" * 5000}, headers=headers)
    assert resp.status_code == 422
