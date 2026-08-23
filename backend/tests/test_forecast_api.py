"""GET /forecasts and GET /forecasts/exhaustion.

These two had no route tests at all: the worker was covered, and the reading
of what it wrote was not. What that missed is below — a forecast outliving the
device it describes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.models import ExhaustionForecast, MetricForecast, User
from app.security.tokens import issue_access_token
from app.services import enrollment_service as svc

NOW = datetime.now(UTC)


def _headers(user_id) -> dict:  # noqa: ANN001
    token, _ = issue_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def two_forecast_devices(admin_session):
    """One live device and one the user has since removed, each with a stored
    forecast and exhaustion projection."""
    user = User(email="forecast-api@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()

    live = await svc.register_device(admin_session, user_id=user.id, name="live-box")
    gone = await svc.register_device(admin_session, user_id=user.id, name="removed-box")

    for device in (live, gone):
        admin_session.add(
            MetricForecast(
                user_id=user.id,
                device_id=device.id,
                metric="mem_percent",
                computed_at=NOW,
                horizon_seconds=86_400,
                bucket_seconds=3600,
                history_seconds=86_400,
                points=[],
            )
        )
        admin_session.add(
            ExhaustionForecast(
                user_id=user.id,
                device_id=device.id,
                metric="mem_percent",
                computed_at=NOW,
                current_value=64.0,
                slope_per_day=3.0,
                projected_at=NOW,
            )
        )
    await admin_session.commit()

    # Remove the second one the way DELETE /devices/{id} does.
    await admin_session.execute(
        sa.text("UPDATE devices SET deleted_at = now() WHERE id = :i"), {"i": gone.id}
    )
    await admin_session.commit()

    return {"user": user, "live": live, "gone": gone}


async def test_a_removed_devices_forecast_is_not_returned(client, two_forecast_devices):
    """A forecast says where a machine is *heading*. Keeping one for a machine
    the user removed states a future for something that no longer exists — and
    since /devices correctly omits deleted devices, the page had no name for it
    and rendered a bare UUID beside "full in 22h". Found by looking at the
    Forecasts page after removing four test phones.
    """
    user_id = two_forecast_devices["user"].id
    live_id = str(two_forecast_devices["live"].id)
    gone_id = str(two_forecast_devices["gone"].id)

    for path in ("/forecasts", "/forecasts/exhaustion"):
        response = await client.get(path, headers=_headers(user_id))
        assert response.status_code == 200
        device_ids = {row["device_id"] for row in response.json()}
        assert gone_id not in device_ids, f"{path} returned a removed device"
        assert device_ids == {live_id}, f"{path} returned {device_ids}"


async def test_the_live_devices_rows_still_come_back(client, two_forecast_devices):
    """The filter must not be so eager it empties the list — the whole point of
    the soft delete is that everything else keeps working."""
    user_id = two_forecast_devices["user"].id

    forecasts = (await client.get("/forecasts", headers=_headers(user_id))).json()
    assert len(forecasts) == 1
    assert forecasts[0]["metric"] == "mem_percent"
    # points=[] is returned rather than omitted, so a caller can still see when
    # the worker last checked — Phase 7's invariant.
    assert forecasts[0]["points"] == []
    assert forecasts[0]["computed_at"] is not None

    exhaustion = (await client.get("/forecasts/exhaustion", headers=_headers(user_id))).json()
    assert len(exhaustion) == 1
    assert exhaustion[0]["current_value"] == 64.0


async def test_device_id_filter_still_narrows_to_one_device(client, two_forecast_devices):
    user_id = two_forecast_devices["user"].id
    live_id = str(two_forecast_devices["live"].id)

    response = await client.get(
        "/forecasts", params={"device_id": live_id}, headers=_headers(user_id)
    )
    assert [row["device_id"] for row in response.json()] == [live_id]


async def test_asking_for_a_removed_device_by_id_returns_nothing(client, two_forecast_devices):
    """The explicit filter must not reintroduce what the implicit one drops —
    the phone's per-device screens pass device_id straight through."""
    user_id = two_forecast_devices["user"].id
    gone_id = str(two_forecast_devices["gone"].id)

    response = await client.get(
        "/forecasts", params={"device_id": gone_id}, headers=_headers(user_id)
    )
    assert response.json() == []
