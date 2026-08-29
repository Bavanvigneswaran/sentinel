"""GET/PATCH /notifications/settings, the VAPID key endpoint, and web push
subscribe/unsubscribe.
"""

from __future__ import annotations

SIGNUP = "/auth/signup"
CREDS = {"email": "notify-api-owner@example.com", "password": "a-perfectly-fine-password"}


async def _auth_headers(client, creds=CREDS) -> dict:
    token = (await client.post(SIGNUP, json=creds)).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_settings_require_authentication(client):
    assert (await client.get("/notifications/settings")).status_code == 401


async def test_get_settings_lazily_creates_defaults(client):
    headers = await _auth_headers(client)
    resp = await client.get("/notifications/settings", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email_enabled"] is False
    assert body["web_push_enabled"] is False
    assert body["anomaly_sensitivity"] == "medium"


async def test_patch_settings_updates_only_given_fields(client):
    headers = await _auth_headers(client)
    await client.get("/notifications/settings", headers=headers)  # lazily create

    patched = await client.patch(
        "/notifications/settings", json={"email_enabled": True}, headers=headers
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["email_enabled"] is True
    assert body["web_push_enabled"] is False  # untouched
    assert body["updated_at"] is not None


async def test_patch_settings_can_set_and_clear_the_email_override(client):
    headers = await _auth_headers(client)

    set_resp = await client.patch(
        "/notifications/settings",
        json={"email_address": "override@example.com"},
        headers=headers,
    )
    assert set_resp.json()["email_address"] == "override@example.com"

    cleared = await client.patch(
        "/notifications/settings", json={"email_address": None}, headers=headers
    )
    assert cleared.json()["email_address"] is None


async def test_patch_settings_rejects_an_invalid_sensitivity(client):
    headers = await _auth_headers(client)
    resp = await client.patch(
        "/notifications/settings", json={"anomaly_sensitivity": "extreme"}, headers=headers
    )
    assert resp.status_code == 422


async def test_vapid_public_key_endpoint(client):
    headers = await _auth_headers(client)
    resp = await client.get("/notifications/vapid-public-key", headers=headers)
    assert resp.status_code == 200
    # Unconfigured in the test environment: reports absence rather than a key.
    assert resp.json() == {"public_key": None}


async def test_web_push_subscribe_and_unsubscribe(client):
    headers = await _auth_headers(client)
    subscribe = await client.post(
        "/notifications/web-push/subscribe",
        json={"endpoint": "https://push.example/abc", "p256dh": "key", "auth": "secret"},
        headers=headers,
    )
    assert subscribe.status_code == 204

    # Re-subscribing the same endpoint upserts rather than conflicting.
    resubscribe = await client.post(
        "/notifications/web-push/subscribe",
        json={"endpoint": "https://push.example/abc", "p256dh": "key2", "auth": "secret2"},
        headers=headers,
    )
    assert resubscribe.status_code == 204

    unsubscribe = await client.request(
        "DELETE",
        "/notifications/web-push/subscribe",
        json={"endpoint": "https://push.example/abc"},
        headers=headers,
    )
    assert unsubscribe.status_code == 204


# --- web push endpoints are request targets, not just strings ----------------


async def test_subscribing_refuses_an_internal_endpoint(client):
    """The server POSTs to this URL on every alert (app/alerts/notify.py), so a
    caller who can choose it can choose where the server sends a request.

    Each of these was accepted before the field was validated."""
    headers = await _auth_headers(client)
    for endpoint in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:6379/",
        "file:///etc/passwd",
        "https://127.0.0.1/push",
        "https://localhost/push",
        "https://10.0.0.5/push",
    ):
        resp = await client.post(
            "/notifications/web-push/subscribe",
            json={"endpoint": endpoint, "p256dh": "key", "auth": "secret"},
            headers=headers,
        )
        assert resp.status_code == 422, f"{endpoint} was accepted: {resp.text}"


async def test_the_refusal_says_why(client):
    headers = await _auth_headers(client)
    resp = await client.post(
        "/notifications/web-push/subscribe",
        json={"endpoint": "http://push.example/abc", "p256dh": "k", "auth": "s"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "https" in resp.text


async def test_unsubscribing_is_deliberately_not_validated(client):
    """A row written before the endpoint was validated must stay removable.

    Deleting by exact string cannot reach anything the caller does not already
    own — RLS scopes the statement and the route filters on user_id too."""
    headers = await _auth_headers(client)
    resp = await client.request(
        "DELETE",
        "/notifications/web-push/subscribe",
        json={"endpoint": "http://127.0.0.1:6379/"},
        headers=headers,
    )
    assert resp.status_code == 204
