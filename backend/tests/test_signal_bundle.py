"""app/services/signal_bundle.py's build_signal_bundle(): assembles a device's
correlated events, current health, and the anomaly-baseline/forecast rows
for whichever metrics are actually involved — nothing invented, nothing
included for a metric the incident never touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.ingest.writer import write_samples
from app.models import AlertEvent, AnomalyBaseline, MetricForecast, User
from app.models.incidents import Incident
from app.schemas.protocol import Sample, SystemSample
from app.services import enrollment_service as svc
from app.services.signal_bundle import build_signal_bundle
from tests.conftest import scoped_session_for


def _now() -> datetime:
    """Evaluated per fixture, not at import.

    A module-level `now = datetime.now(UTC)` made this file's freshness
    assertions depend on how long the *whole suite* took to reach them: the
    device's `last_seen_at` is compared against DEVICE_STALE_AFTER_SECONDS
    (45s), so once the suite grew past that the device read as offline and the
    health score came back "unknown". The failure had nothing to do with the
    code under test.
    """
    return datetime.now(UTC)


@pytest.fixture
async def incident_with_events(admin_session):
    now = _now()
    user = User(email="bundle-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="bundle-box")

    # A fresh, online-looking reading so the health score isn't "unknown".
    await write_samples(
        admin_session,
        device_id=device.id,
        user_id=user.id,
        samples=[
            Sample(
                ts=now,
                resolution_seconds=10,
                system=SystemSample(cpu_percent=91.0, mem_percent=40.0),
            )
        ],
        now=now,
    )
    from sqlalchemy import update

    from app.models import Device

    await admin_session.execute(
        update(Device).where(Device.id == device.id).values(status="online", last_seen_at=now)
    )
    await admin_session.commit()

    incident = Incident(user_id=user.id, device_id=device.id, status="open", opened_at=now)
    admin_session.add(incident)
    await admin_session.commit()

    threshold_event = AlertEvent(
        user_id=user.id,
        device_id=device.id,
        incident_id=incident.id,
        rule_name="High CPU",
        rule_type="threshold",
        metric="cpu_percent",
        comparison=">",
        threshold=80.0,
        status="firing",
        value_at_fire=91.0,
        last_value=91.0,
        fired_at=now,
    )
    admin_session.add(threshold_event)

    # A baseline and a forecast for cpu_percent (the involved metric)...
    admin_session.add(
        AnomalyBaseline(
            user_id=user.id,
            device_id=device.id,
            metric="cpu_percent",
            mean=30.0,
            mad=5.0,
            sample_count=50,
        )
    )
    admin_session.add(
        MetricForecast(
            user_id=user.id,
            device_id=device.id,
            metric="cpu_percent",
            computed_at=now,
            horizon_seconds=3600,
            bucket_seconds=60,
            points=[
                {"offset_seconds": 60, "predicted": 92.0, "lower": 85.0, "upper": 99.0},
                {"offset_seconds": 3600, "predicted": 97.0, "lower": 80.0, "upper": 100.0},
            ],
        )
    )
    # ...and one for mem_percent, which this incident never touched.
    admin_session.add(
        MetricForecast(
            user_id=user.id,
            device_id=device.id,
            metric="mem_percent",
            computed_at=now,
            horizon_seconds=3600,
            bucket_seconds=60,
            points=[{"offset_seconds": 60, "predicted": 41.0, "lower": 38.0, "upper": 44.0}],
        )
    )
    await admin_session.commit()

    return {"user": user, "device": device, "incident": incident}


async def test_bundle_includes_device_events_and_health(incident_with_events):
    user, device, incident = (
        incident_with_events["user"],
        incident_with_events["device"],
        incident_with_events["incident"],
    )
    async with scoped_session_for(user.id) as session:
        bundle = await build_signal_bundle(session, incident)

    assert bundle.device.name == device.name
    assert len(bundle.events) == 1
    assert bundle.events[0].rule_name == "High CPU"
    assert bundle.events[0].metric == "cpu_percent"
    assert bundle.health.score is not None  # device is online with a fresh reading


async def test_bundle_only_includes_forecasts_and_baselines_for_involved_metrics(
    incident_with_events,
):
    user, incident = incident_with_events["user"], incident_with_events["incident"]
    async with scoped_session_for(user.id) as session:
        bundle = await build_signal_bundle(session, incident)

    assert [f.metric for f in bundle.forecasts] == ["cpu_percent"]
    assert [b.metric for b in bundle.anomaly_baselines] == ["cpu_percent"]
    assert bundle.forecasts[0].first_predicted == 92.0
    assert bundle.forecasts[0].last_predicted == 97.0


async def test_bundle_serializes_to_json_safe_dict(incident_with_events):
    user, incident = incident_with_events["user"], incident_with_events["incident"]
    async with scoped_session_for(user.id) as session:
        bundle = await build_signal_bundle(session, incident)

    import json

    payload = bundle.to_json_dict()
    json.dumps(payload)  # must not raise — every value is JSON-serializable
    assert payload["events"][0]["rule_name"] == "High CPU"
    assert payload["incident_status"] == "open"


async def test_an_incident_with_no_forecast_history_gets_empty_lists(admin_session):
    now = _now()
    user = User(email="bundle-empty-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="empty-bundle-box")

    incident = Incident(
        user_id=user.id, device_id=device.id, status="open", opened_at=now - timedelta(minutes=1)
    )
    admin_session.add(incident)
    event = AlertEvent(
        user_id=user.id,
        device_id=device.id,
        incident_id=None,
        rule_name="High CPU",
        rule_type="threshold",
        metric="cpu_percent",
        comparison=">",
        threshold=80.0,
        status="firing",
        value_at_fire=91.0,
        fired_at=now,
    )
    admin_session.add(event)
    await admin_session.commit()
    event.incident_id = incident.id
    await admin_session.commit()

    async with scoped_session_for(user.id) as session:
        bundle = await build_signal_bundle(session, incident)

    assert bundle.forecasts == ()
    assert bundle.anomaly_baselines == ()
    assert bundle.exhaustion == ()
    assert bundle.health.score is None  # never connected -> unknown, not synthesized
