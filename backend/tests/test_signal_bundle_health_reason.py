"""build_signal_bundle()'s health fallback: an incident on a device that was
removed must say so, not report the device as missing.

Split out rather than folded into test_insights_service.py because it is
about the bundle, not the caching decision. The distinction it guards is the
one lib/deviceNames.ts keeps on both frontends: "removed" is permanent and
explainable, "not found" is a fault — and conflating them invents an
explanation. Caught by generating text for real incidents, every one of
which belonged to a removed device and read "device not found".
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models import User
from app.models.incidents import Incident
from app.services import enrollment_service as svc
from app.services.signal_bundle import build_signal_bundle

NOW = datetime.now(UTC)


@pytest.fixture
async def incident_on(admin_session):
    async def _make(email: str, *, removed: bool):
        user = User(email=email, password_hash="x")
        admin_session.add(user)
        await admin_session.commit()
        device = await svc.register_device(admin_session, user_id=user.id, name="bundle-box")
        if removed:
            device.deleted_at = NOW
            await admin_session.commit()
        incident = Incident(user_id=user.id, device_id=device.id, status="open", opened_at=NOW)
        admin_session.add(incident)
        await admin_session.commit()
        return incident

    return _make


async def test_a_removed_devices_incident_says_removed_not_not_found(admin_session, incident_on):
    incident = await incident_on("bundle-removed@example.com", removed=True)
    bundle = await build_signal_bundle(admin_session, incident)

    assert bundle.health.score is None
    assert bundle.health.reason == "device has been removed"


async def test_a_live_device_with_no_readings_keeps_the_vaguer_wording(admin_session, incident_on):
    """A device that exists and simply has nothing fresh is a different
    state, and must not be described as removed."""
    incident = await incident_on("bundle-quiet@example.com", removed=False)
    bundle = await build_signal_bundle(admin_session, incident)

    assert bundle.health.reason != "device has been removed"
