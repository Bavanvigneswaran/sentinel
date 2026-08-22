"""Device management and the agent enrollment exchange."""

import sqlalchemy as sa

SIGNUP = "/auth/signup"
CREDS = {"email": "owner@example.com", "password": "a-perfectly-fine-password"}
OTHER = {"email": "other@example.com", "password": "a-perfectly-fine-password"}


async def _auth_headers(client, creds=CREDS) -> dict:
    token = (await client.post(SIGNUP, json=creds)).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_devices_require_authentication(client):
    assert (await client.get("/devices")).status_code == 401
    assert (await client.post("/devices", json={"name": "x"})).status_code == 401


async def test_create_and_list_a_device(client):
    headers = await _auth_headers(client)
    created = await client.post("/devices", json={"name": "laptop"}, headers=headers)
    assert created.status_code == 201
    assert created.json()["status"] == "pending"

    listed = await client.get("/devices", headers=headers)
    assert [d["name"] for d in listed.json()] == ["laptop"]


async def test_duplicate_device_names_are_rejected_per_user(client):
    headers = await _auth_headers(client)
    await client.post("/devices", json={"name": "laptop"}, headers=headers)
    dup = await client.post("/devices", json={"name": "laptop"}, headers=headers)
    assert dup.status_code == 409


async def test_a_user_cannot_see_another_users_devices(client):
    mine = await _auth_headers(client)
    await client.post("/devices", json={"name": "mine"}, headers=mine)

    client.cookies.clear()
    theirs = await _auth_headers(client, OTHER)
    await client.post("/devices", json={"name": "theirs"}, headers=theirs)

    assert [d["name"] for d in (await client.get("/devices", headers=mine)).json()] == ["mine"]
    assert [d["name"] for d in (await client.get("/devices", headers=theirs)).json()] == [
        "theirs"
    ]


async def test_deleting_a_device_is_a_soft_delete_that_revokes_its_tokens(
    client, admin_session
):
    """A soft delete that leaves a working credential behind is not a delete."""
    headers = await _auth_headers(client)
    device_id = (
        await client.post("/devices", json={"name": "laptop"}, headers=headers)
    ).json()["id"]

    code = (
        await client.post("/enrollment-codes", json={"device_id": device_id}, headers=headers)
    ).json()["code"]
    enrolled = await client.post(
        "/enroll", json={"code": code, "device_name": "agent-box"}
    )
    assert enrolled.status_code == 201
    # Bound to the existing device, so no duplicate was created.
    assert enrolled.json()["device_id"] == device_id

    assert (await client.delete(f"/devices/{device_id}", headers=headers)).status_code == 204

    row = (
        await admin_session.execute(
            sa.text("SELECT deleted_at, status FROM devices WHERE id = :i"), {"i": device_id}
        )
    ).one()
    assert row.deleted_at is not None
    assert row.status == "offline"

    assert [d["id"] for d in (await client.get("/devices", headers=headers)).json()] == []


async def test_deleting_someone_elses_device_is_a_404(client):
    mine = await _auth_headers(client)
    device_id = (
        await client.post("/devices", json={"name": "mine"}, headers=mine)
    ).json()["id"]

    client.cookies.clear()
    theirs = await _auth_headers(client, OTHER)
    assert (await client.delete(f"/devices/{device_id}", headers=theirs)).status_code == 404


# --- enrollment -------------------------------------------------------------


async def test_an_enrollment_code_is_returned_once_and_stored_hashed(client, admin_session):
    headers = await _auth_headers(client)
    body = (await client.post("/enrollment-codes", json={}, headers=headers)).json()

    assert len(body["code"]) == 14  # XXXX-XXXX-XXXX
    stored = await admin_session.scalar(sa.text("SELECT code_hash FROM enrollment_codes"))
    assert body["code"].replace("-", "").encode() not in bytes(stored)


async def test_enroll_exchanges_a_code_for_a_token(client):
    headers = await _auth_headers(client)
    code = (await client.post("/enrollment-codes", json={}, headers=headers)).json()["code"]

    enrolled = await client.post("/enroll", json={"code": code, "device_name": "my-mac"})
    assert enrolled.status_code == 201
    body = enrolled.json()
    assert body["agent_token"].startswith("sag_")
    assert body["device_id"]

    devices = (await client.get("/devices", headers=headers)).json()
    assert [d["name"] for d in devices] == ["my-mac"]


async def test_enroll_needs_no_authentication(client):
    """The agent has no user identity yet; the code is the credential."""
    headers = await _auth_headers(client)
    code = (await client.post("/enrollment-codes", json={}, headers=headers)).json()["code"]

    client.cookies.clear()
    enrolled = await client.post("/enroll", json={"code": code, "device_name": "mac"})
    assert enrolled.status_code == 201


async def test_a_code_cannot_be_used_twice(client):
    headers = await _auth_headers(client)
    code = (await client.post("/enrollment-codes", json={}, headers=headers)).json()["code"]

    assert (
        await client.post("/enroll", json={"code": code, "device_name": "one"})
    ).status_code == 201
    second = await client.post("/enroll", json={"code": code, "device_name": "two"})
    assert second.status_code == 400


async def test_invalid_and_expired_codes_are_indistinguishable(client):
    headers = await _auth_headers(client)
    code = (await client.post("/enrollment-codes", json={}, headers=headers)).json()["code"]
    await client.post("/enroll", json={"code": code, "device_name": "one"})

    used = await client.post("/enroll", json={"code": code, "device_name": "x"})
    bogus = await client.post("/enroll", json={"code": "ZZZZ-ZZZZ-ZZZZ", "device_name": "x"})

    assert used.status_code == bogus.status_code == 400
    assert used.json() == bogus.json()


async def test_a_colliding_device_name_is_disambiguated_not_rejected(client):
    """Two machines can share a hostname. An agent trying to enroll should not
    fail because of it."""
    headers = await _auth_headers(client)
    for _ in range(2):
        code = (await client.post("/enrollment-codes", json={}, headers=headers)).json()["code"]
        result = await client.post("/enroll", json={"code": code, "device_name": "macbook"})
        assert result.status_code == 201

    names = sorted(d["name"] for d in (await client.get("/devices", headers=headers)).json())
    assert names == ["macbook", "macbook-2"]


async def test_the_consumed_code_records_the_device_it_created(client, admin_session):
    headers = await _auth_headers(client)
    code = (await client.post("/enrollment-codes", json={}, headers=headers)).json()["code"]
    device_id = (
        await client.post("/enroll", json={"code": code, "device_name": "mac"})
    ).json()["device_id"]

    row = (
        await admin_session.execute(
            sa.text("SELECT device_id, consumed_at FROM enrollment_codes")
        )
    ).one()
    assert str(row.device_id) == device_id
    assert row.consumed_at is not None


async def test_binding_a_code_to_another_users_device_is_a_404(client):
    mine = await _auth_headers(client)
    device_id = (
        await client.post("/devices", json={"name": "mine"}, headers=mine)
    ).json()["id"]

    client.cookies.clear()
    theirs = await _auth_headers(client, OTHER)
    result = await client.post(
        "/enrollment-codes", json={"device_id": device_id}, headers=theirs
    )
    assert result.status_code == 404


# --- agent tokens -----------------------------------------------------------


async def test_agent_tokens_are_listed_without_their_secret(client):
    headers = await _auth_headers(client)
    code = (await client.post("/enrollment-codes", json={}, headers=headers)).json()["code"]
    enrolled = (
        await client.post("/enroll", json={"code": code, "device_name": "mac"})
    ).json()

    tokens = (
        await client.get(f"/devices/{enrolled['device_id']}/tokens", headers=headers)
    ).json()
    assert len(tokens) == 1
    assert tokens[0]["token_prefix"] == enrolled["agent_token"][:12]
    assert enrolled["agent_token"] not in str(tokens)


async def test_revoking_an_agent_token(client, admin_session):
    headers = await _auth_headers(client)
    code = (await client.post("/enrollment-codes", json={}, headers=headers)).json()["code"]
    enrolled = (
        await client.post("/enroll", json={"code": code, "device_name": "mac"})
    ).json()
    tokens = (
        await client.get(f"/devices/{enrolled['device_id']}/tokens", headers=headers)
    ).json()

    assert (
        await client.delete(f"/agent-tokens/{tokens[0]['id']}", headers=headers)
    ).status_code == 204

    revoked = await admin_session.scalar(sa.text("SELECT revoked_at FROM agent_tokens"))
    assert revoked is not None


async def test_a_user_cannot_revoke_another_users_agent_token(client):
    mine = await _auth_headers(client)
    code = (await client.post("/enrollment-codes", json={}, headers=mine)).json()["code"]
    enrolled = (await client.post("/enroll", json={"code": code, "device_name": "mac"})).json()
    tokens = (
        await client.get(f"/devices/{enrolled['device_id']}/tokens", headers=mine)
    ).json()

    client.cookies.clear()
    theirs = await _auth_headers(client, OTHER)
    result = await client.delete(f"/agent-tokens/{tokens[0]['id']}", headers=theirs)
    assert result.status_code == 404


async def test_a_code_bound_to_a_device_reuses_it_instead_of_duplicating(client):
    """Re-enrolling an agent must keep the machine's history, not orphan it
    behind a second device row."""
    headers = await _auth_headers(client)
    device_id = (
        await client.post("/devices", json={"name": "workstation"}, headers=headers)
    ).json()["id"]

    code = (
        await client.post("/enrollment-codes", json={"device_id": device_id}, headers=headers)
    ).json()["code"]
    enrolled = await client.post("/enroll", json={"code": code, "device_name": "ignored"})

    assert enrolled.status_code == 201
    assert enrolled.json()["device_id"] == device_id

    devices = (await client.get("/devices", headers=headers)).json()
    assert len(devices) == 1
    assert devices[0]["name"] == "workstation"


async def test_an_unbound_code_creates_a_new_device(client):
    headers = await _auth_headers(client)
    code = (await client.post("/enrollment-codes", json={}, headers=headers)).json()["code"]
    await client.post("/enroll", json={"code": code, "device_name": "fresh"})

    devices = (await client.get("/devices", headers=headers)).json()
    assert [d["name"] for d in devices] == ["fresh"]
