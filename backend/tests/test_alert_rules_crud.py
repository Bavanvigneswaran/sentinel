"""GET/POST/PATCH/DELETE /alerts/rules."""

from __future__ import annotations

SIGNUP = "/auth/signup"
CREDS = {"email": "rule-owner@example.com", "password": "a-perfectly-fine-password"}
OTHER = {"email": "rule-stranger@example.com", "password": "a-perfectly-fine-password"}

RULE = {
    "name": "High CPU",
    "metric": "cpu_percent",
    "comparison": ">",
    "threshold": 90.0,
    "for_duration_seconds": 120,
}

ANOMALY_RULE = {
    "name": "Unusual CPU",
    "rule_type": "anomaly",
    "metric": "cpu_percent",
    "for_duration_seconds": 120,
}


async def _auth_headers(client, creds=CREDS) -> dict:
    token = (await client.post(SIGNUP, json=creds)).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_rules_require_authentication(client):
    assert (await client.get("/alerts/rules")).status_code == 401
    assert (await client.post("/alerts/rules", json=RULE)).status_code == 401


async def test_create_and_list_a_fleet_wide_rule(client):
    headers = await _auth_headers(client)
    created = await client.post("/alerts/rules", json=RULE, headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert body["device_id"] is None
    assert body["enabled"] is True
    assert body["for_duration_seconds"] == 120

    listed = await client.get("/alerts/rules", headers=headers)
    assert [r["name"] for r in listed.json()] == ["High CPU"]


async def test_a_rule_against_someone_elses_device_is_a_404(client):
    mine = await _auth_headers(client)
    theirs = await _auth_headers(client, OTHER)
    their_device = (
        await client.post("/devices", json={"name": "their-box"}, headers=theirs)
    ).json()

    resp = await client.post(
        "/alerts/rules", json={**RULE, "device_id": their_device["id"]}, headers=mine
    )
    assert resp.status_code == 404


async def test_a_rule_against_a_nonexistent_device_is_a_404(client):
    headers = await _auth_headers(client)
    resp = await client.post(
        "/alerts/rules",
        json={**RULE, "device_id": "00000000-0000-0000-0000-000000000000"},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_create_a_device_scoped_rule(client):
    headers = await _auth_headers(client)
    device = (await client.post("/devices", json={"name": "box"}, headers=headers)).json()

    created = await client.post(
        "/alerts/rules", json={**RULE, "device_id": device["id"]}, headers=headers
    )
    assert created.status_code == 201
    assert created.json()["device_id"] == device["id"]


async def test_patch_updates_only_the_given_fields(client):
    headers = await _auth_headers(client)
    rule = (await client.post("/alerts/rules", json=RULE, headers=headers)).json()

    patched = await client.patch(
        f"/alerts/rules/{rule['id']}", json={"threshold": 95.0}, headers=headers
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["threshold"] == 95.0
    assert body["name"] == "High CPU"  # untouched


async def test_patch_on_a_missing_rule_is_404(client):
    headers = await _auth_headers(client)
    resp = await client.patch(
        "/alerts/rules/00000000-0000-0000-0000-000000000000",
        json={"threshold": 1.0},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_delete_a_rule(client):
    headers = await _auth_headers(client)
    rule = (await client.post("/alerts/rules", json=RULE, headers=headers)).json()

    assert (
        await client.delete(f"/alerts/rules/{rule['id']}", headers=headers)
    ).status_code == 204
    assert (await client.get("/alerts/rules", headers=headers)).json() == []
    assert (
        await client.delete(f"/alerts/rules/{rule['id']}", headers=headers)
    ).status_code == 404


async def test_create_an_anomaly_rule_without_comparison_or_threshold(client):
    headers = await _auth_headers(client)
    created = await client.post("/alerts/rules", json=ANOMALY_RULE, headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert body["rule_type"] == "anomaly"
    assert body["comparison"] is None
    assert body["threshold"] is None


async def test_an_anomaly_rule_with_a_comparison_is_rejected(client):
    headers = await _auth_headers(client)
    resp = await client.post(
        "/alerts/rules", json={**ANOMALY_RULE, "comparison": ">"}, headers=headers
    )
    assert resp.status_code == 422


async def test_a_threshold_rule_missing_its_threshold_is_rejected(client):
    headers = await _auth_headers(client)
    payload = {k: v for k, v in RULE.items() if k != "threshold"}
    resp = await client.post("/alerts/rules", json=payload, headers=headers)
    assert resp.status_code == 422


async def test_patching_an_anomaly_rules_duration_alone_leaves_its_type_untouched(client):
    headers = await _auth_headers(client)
    rule = (await client.post("/alerts/rules", json=ANOMALY_RULE, headers=headers)).json()

    patched = await client.patch(
        f"/alerts/rules/{rule['id']}", json={"for_duration_seconds": 30}, headers=headers
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["rule_type"] == "anomaly"
    assert body["for_duration_seconds"] == 30
    assert body["comparison"] is None
    assert body["threshold"] is None


async def test_flipping_rule_type_without_paired_fields_is_rejected(client):
    headers = await _auth_headers(client)
    rule = (await client.post("/alerts/rules", json=ANOMALY_RULE, headers=headers)).json()

    # Flip to threshold without supplying comparison/threshold in the same
    # request: the schema validator itself rejects this.
    resp = await client.patch(
        f"/alerts/rules/{rule['id']}", json={"rule_type": "threshold"}, headers=headers
    )
    assert resp.status_code == 422

    threshold_rule = (await client.post("/alerts/rules", json=RULE, headers=headers)).json()
    # Flip to anomaly while still carrying comparison/threshold: also
    # rejected by the schema validator.
    resp = await client.patch(
        f"/alerts/rules/{threshold_rule['id']}",
        json={"rule_type": "anomaly"},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_setting_threshold_alone_on_an_existing_anomaly_rule_is_rejected(client):
    """rule_type isn't in this payload, so AlertRuleUpdate's own validator
    has nothing to check — this exercises the route's IntegrityError-to-422
    translation of the DB's rule_type_fields CHECK constraint instead."""
    headers = await _auth_headers(client)
    rule = (await client.post("/alerts/rules", json=ANOMALY_RULE, headers=headers)).json()

    resp = await client.patch(
        f"/alerts/rules/{rule['id']}", json={"threshold": 95.0}, headers=headers
    )
    assert resp.status_code == 422


async def test_a_user_cannot_see_another_users_rules(client):
    mine = await _auth_headers(client)
    theirs = await _auth_headers(client, OTHER)
    await client.post("/alerts/rules", json=RULE, headers=mine)
    await client.post(
        "/alerts/rules", json={**RULE, "name": "theirs"}, headers=theirs
    )

    assert [r["name"] for r in (await client.get("/alerts/rules", headers=mine)).json()] == [
        "High CPU"
    ]
    assert [
        r["name"] for r in (await client.get("/alerts/rules", headers=theirs)).json()
    ] == ["theirs"]
