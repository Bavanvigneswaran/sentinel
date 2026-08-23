"""GET/POST/PATCH/DELETE /reports/schedules, and tenancy across two users —
same style as test_alert_rules_crud.py.
"""

from __future__ import annotations

SIGNUP = "/auth/signup"
CREDS = {"email": "report-owner@example.com", "password": "a-perfectly-fine-password"}
OTHER = {"email": "report-stranger@example.com", "password": "a-perfectly-fine-password"}

WEEKLY = {
    "name": "Weekly fleet summary",
    "cadence": "weekly",
    "day_of_week": 0,
    "format": "pdf",
}

MONTHLY = {
    "name": "Monthly CSV export",
    "cadence": "monthly",
    "day_of_month": 1,
    "format": "csv",
    "period_days": 30,
}


async def _auth_headers(client, creds=CREDS) -> dict:
    token = (await client.post(SIGNUP, json=creds)).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_schedules_require_authentication(client):
    assert (await client.get("/reports/schedules")).status_code == 401
    assert (await client.post("/reports/schedules", json=WEEKLY)).status_code == 401


async def test_create_and_list_a_fleet_wide_weekly_schedule(client):
    headers = await _auth_headers(client)
    created = await client.post("/reports/schedules", json=WEEKLY, headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert body["device_id"] is None
    assert body["cadence"] == "weekly"
    assert body["day_of_week"] == 0
    assert body["day_of_month"] is None
    assert body["enabled"] is True
    assert body["recipients"] == []
    assert body["last_sent_at"] is None

    listed = await client.get("/reports/schedules", headers=headers)
    assert [s["name"] for s in listed.json()] == ["Weekly fleet summary"]


async def test_create_a_monthly_csv_schedule(client):
    headers = await _auth_headers(client)
    created = await client.post("/reports/schedules", json=MONTHLY, headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert body["cadence"] == "monthly"
    assert body["day_of_month"] == 1
    assert body["format"] == "csv"


async def test_weekly_schedule_without_day_of_week_is_rejected(client):
    headers = await _auth_headers(client)
    payload = {k: v for k, v in WEEKLY.items() if k != "day_of_week"}
    resp = await client.post("/reports/schedules", json=payload, headers=headers)
    assert resp.status_code == 422


async def test_weekly_schedule_with_day_of_month_is_rejected(client):
    headers = await _auth_headers(client)
    resp = await client.post(
        "/reports/schedules", json={**WEEKLY, "day_of_month": 5}, headers=headers
    )
    assert resp.status_code == 422


async def test_a_schedule_against_someone_elses_device_is_a_404(client):
    mine = await _auth_headers(client)
    theirs = await _auth_headers(client, OTHER)
    their_device = (
        await client.post("/devices", json={"name": "their-box"}, headers=theirs)
    ).json()

    resp = await client.post(
        "/reports/schedules", json={**WEEKLY, "device_id": their_device["id"]}, headers=mine
    )
    assert resp.status_code == 404


async def test_patch_updates_only_the_given_fields(client):
    headers = await _auth_headers(client)
    schedule = (await client.post("/reports/schedules", json=WEEKLY, headers=headers)).json()

    patched = await client.patch(
        f"/reports/schedules/{schedule['id']}", json={"enabled": False}, headers=headers
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["enabled"] is False
    assert body["name"] == "Weekly fleet summary"  # untouched


async def test_patch_on_a_missing_schedule_is_404(client):
    headers = await _auth_headers(client)
    resp = await client.patch(
        "/reports/schedules/00000000-0000-0000-0000-000000000000",
        json={"enabled": False},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_delete_a_schedule(client):
    headers = await _auth_headers(client)
    schedule = (await client.post("/reports/schedules", json=WEEKLY, headers=headers)).json()

    assert (
        await client.delete(f"/reports/schedules/{schedule['id']}", headers=headers)
    ).status_code == 204
    assert (await client.get("/reports/schedules", headers=headers)).json() == []
    assert (
        await client.delete(f"/reports/schedules/{schedule['id']}", headers=headers)
    ).status_code == 404


async def test_a_user_cannot_see_another_users_schedules(client):
    mine = await _auth_headers(client)
    theirs = await _auth_headers(client, OTHER)
    await client.post("/reports/schedules", json=WEEKLY, headers=mine)
    await client.post("/reports/schedules", json={**MONTHLY, "name": "theirs"}, headers=theirs)

    assert [
        s["name"] for s in (await client.get("/reports/schedules", headers=mine)).json()
    ] == ["Weekly fleet summary"]
    assert [
        s["name"] for s in (await client.get("/reports/schedules", headers=theirs)).json()
    ] == ["theirs"]
