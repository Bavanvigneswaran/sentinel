"""An account detects things without having been configured.

Every detector in this system worked before this and every one of them sat
idle until somebody hand-wrote a rule, so a new account with machines enrolled
was silent through a disk filling to 100%. These tests pin the seeding that
closes that, and — more importantly — the two properties that make a default
rule set tolerable rather than annoying: it covers devices enrolled later, and
it never comes back once you have thrown it away.
"""

from __future__ import annotations

from app.alerts.defaults import DEFAULT_RULES, SOURCE_BUILTIN, ensure_default_rules

SIGNUP = "/auth/signup"
CREDS = {"email": "defaults-owner@example.com", "password": "a-perfectly-fine-password"}


async def _auth_headers(client, creds=CREDS) -> dict:
    token = (await client.post(SIGNUP, json=creds)).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_a_brand_new_account_already_has_detection(client):
    headers = await _auth_headers(client)

    rules = (await client.get("/alerts/rules", headers=headers)).json()

    assert len(rules) == len(DEFAULT_RULES)
    assert {r["source"] for r in rules} == {SOURCE_BUILTIN}
    assert all(r["enabled"] for r in rules)


async def test_the_defaults_cover_all_three_kinds_of_detection(client):
    """The point of the set, not an incidental property of it: a fixed
    threshold cannot notice a machine that normally idles at 8% running at
    45%, and neither can notice a disk that will be full on Thursday."""
    headers = await _auth_headers(client)

    rules = (await client.get("/alerts/rules", headers=headers)).json()

    assert {r["rule_type"] for r in rules} == {"threshold", "anomaly", "forecast"}


async def test_every_default_is_fleet_wide_so_later_devices_are_covered(client):
    """device_id=None is load-bearing. The evaluator fans a null-device rule
    out across the account's devices at sweep time, so a machine enrolled
    tomorrow is covered by rules seeded today with nothing re-running."""
    headers = await _auth_headers(client)

    rules = (await client.get("/alerts/rules", headers=headers)).json()

    assert [r["device_id"] for r in rules] == [None] * len(DEFAULT_RULES)


async def test_a_default_can_be_edited_like_any_other_rule(client):
    """A default you cannot tune is a default people work around."""
    headers = await _auth_headers(client)
    rule = next(
        r
        for r in (await client.get("/alerts/rules", headers=headers)).json()
        if r["rule_type"] == "threshold"
    )

    resp = await client.patch(
        f"/alerts/rules/{rule['id']}", json={"threshold": 75.0}, headers=headers
    )

    assert resp.status_code == 200
    assert resp.json()["threshold"] == 75.0
    # Still marked as where it came from — editing it does not make it yours,
    # it makes it a tuned default, which is a different and useful thing to
    # be able to see.
    assert resp.json()["source"] == SOURCE_BUILTIN


async def test_a_default_can_be_disabled(client):
    headers = await _auth_headers(client)
    rule = (await client.get("/alerts/rules", headers=headers)).json()[0]

    resp = await client.patch(
        f"/alerts/rules/{rule['id']}", json={"enabled": False}, headers=headers
    )

    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


async def test_a_deleted_default_stays_deleted(admin_session, client):
    """The property that makes re-seeding safe, and the reason
    ensure_default_rules asks "has this account ever been seeded" rather than
    "does it have all eight right now". A default that reappears after you
    delete it is not a default; it is a bug you cannot work around."""
    import sqlalchemy as sa

    from app.models import User

    headers = await _auth_headers(client)
    rules = (await client.get("/alerts/rules", headers=headers)).json()
    doomed = rules[0]

    assert (
        await client.delete(f"/alerts/rules/{doomed['id']}", headers=headers)
    ).status_code == 204

    # Run the seeding again, exactly as a second signup or a re-run backfill
    # would. It must add nothing.
    user_id = await admin_session.scalar(
        sa.select(User.id).where(User.email == CREDS["email"])
    )
    added = await ensure_default_rules(admin_session, user_id)
    await admin_session.commit()

    assert added == 0

    remaining = (await client.get("/alerts/rules", headers=headers)).json()
    assert len(remaining) == len(DEFAULT_RULES) - 1
    assert doomed["name"] not in [r["name"] for r in remaining]


async def test_seeding_is_per_account(client):
    """Two accounts, two independent sets — not one shared set, and not one
    account's edits leaking into another's."""
    mine = await _auth_headers(client)
    theirs = await _auth_headers(
        client, {"email": "defaults-other@example.com", "password": "a-perfectly-fine-password"}
    )

    my_rules = (await client.get("/alerts/rules", headers=mine)).json()
    their_rules = (await client.get("/alerts/rules", headers=theirs)).json()

    assert len(my_rules) == len(their_rules) == len(DEFAULT_RULES)
    assert {r["id"] for r in my_rules}.isdisjoint({r["id"] for r in their_rules})
