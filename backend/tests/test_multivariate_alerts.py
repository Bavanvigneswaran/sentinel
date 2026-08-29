"""The rule_type="multivariate" path: layer 4 wired into alerting.

Two things are being asserted, and the second matters more than the first.

That a novelty spike drives the ordinary OK -> PENDING -> FIRING machine and
opens an ordinary AlertEvent — i.e. that this reuses the pipeline rather than
adding a parallel one, which is the refusal Phases 6, 7 and 8 each made.

And that the constraint pair holds in both directions: `novelty_score` is
judged only by multivariate rules, and a multivariate rule judges only
`novelty_score`. The reverse direction is the one worth a test, because a
threshold rule pointed at novelty_score would be accepted, read perfectly
plausibly, and then silently never fire — nothing writes that column.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import joblib
import pytest
import sqlalchemy as sa

from app.alerts.multivariate_eval import evaluate_multivariate_pair
from app.analysis.multivariate import train
from app.config import get_settings
from app.models import AlertEvent, AlertRule, AlertState, Device, MetricSample, User
from app.security.tokens import issue_access_token
from app.services import enrollment_service as svc
from app.services import model_integrity, novelty_service

NOW = datetime.now(UTC)


def _headers(user_id) -> dict:
    token, _ = issue_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _reading(**overrides) -> dict:
    base = {
        "cpu_percent": 8.0,
        "cpu_user_percent": 5.0,
        "cpu_system_percent": 3.0,
        "mem_percent": 55.0,
        "swap_percent": 0.0,
        "load1": 1.5,
        "process_count": 420,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _clear_model_cache():
    novelty_service._CACHE.clear()
    yield
    novelty_service._CACHE.clear()


@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "novelty_model_dir", str(tmp_path), raising=False)
    return tmp_path


@pytest.fixture
async def owned_device(admin_session):
    user = User(email=f"mv-{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="mv-box")
    return {"session": admin_session, "user": user, "device": device}


def _write_model(directory, device_id):
    import numpy as np

    rng = np.random.default_rng(11)
    rows = [
        _reading(
            cpu_percent=float(abs(rng.normal(8, 2))),
            mem_percent=float(rng.normal(55, 3)),
            load1=float(abs(rng.normal(1.5, 0.4))),
        )
        for _ in range(400)
    ]
    model = train(rows)
    assert model is not None
    _path = directory / f"{device_id}.joblib"
    joblib.dump(model, _path)
    # An unsigned model is refused at load — model_integrity.py.
    model_integrity.sign(_path)


def _rule(user_id, device_id, **overrides) -> AlertRule:
    kwargs = {
        "user_id": user_id,
        "device_id": device_id,
        "name": "Unusual combination",
        "rule_type": "multivariate",
        "metric": "novelty_score",
        "comparison": ">",
        "threshold": 90.0,
        "for_duration_seconds": 0,
        "enabled": True,
    }
    kwargs.update(overrides)
    return AlertRule(**kwargs)


# --- the constraint pair ---------------------------------------------------


async def test_a_multivariate_rule_is_accepted_through_the_api(client, owned_device):
    user, device = owned_device["user"], owned_device["device"]
    r = await client.post(
        "/alerts/rules",
        headers=_headers(user.id),
        json={
            "device_id": str(device.id),
            "name": "Unusual combination",
            "rule_type": "multivariate",
            "metric": "novelty_score",
            "comparison": ">",
            "threshold": 95,
            "for_duration_seconds": 300,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["rule_type"] == "multivariate"
    assert r.json()["metric"] == "novelty_score"


async def test_a_threshold_rule_cannot_be_pointed_at_novelty_score(client, owned_device):
    """The direction that would fail silently: accepted, plausible, never
    fires, because nothing writes that column."""
    user, device = owned_device["user"], owned_device["device"]
    r = await client.post(
        "/alerts/rules",
        headers=_headers(user.id),
        json={
            "device_id": str(device.id),
            "name": "Sneaky",
            "rule_type": "threshold",
            "metric": "novelty_score",
            "comparison": ">",
            "threshold": 95,
        },
    )
    assert r.status_code == 422


async def test_a_multivariate_rule_cannot_be_pointed_at_a_measured_metric(client, owned_device):
    user, device = owned_device["user"], owned_device["device"]
    r = await client.post(
        "/alerts/rules",
        headers=_headers(user.id),
        json={
            "device_id": str(device.id),
            "name": "Confused",
            "rule_type": "multivariate",
            "metric": "cpu_percent",
            "comparison": ">",
            "threshold": 95,
        },
    )
    assert r.status_code == 422


async def test_a_multivariate_rule_requires_comparison_and_threshold(client, owned_device):
    user, device = owned_device["user"], owned_device["device"]
    r = await client.post(
        "/alerts/rules",
        headers=_headers(user.id),
        json={
            "device_id": str(device.id),
            "name": "Incomplete",
            "rule_type": "multivariate",
            "metric": "novelty_score",
        },
    )
    assert r.status_code == 422


async def test_the_database_refuses_the_bad_pair_even_past_the_api(owned_device):
    """The Pydantic validator is the friendly boundary; the CHECK is the one
    that actually holds. Asserted separately so removing either is a failure."""
    session, user, device = (
        owned_device["session"],
        owned_device["user"],
        owned_device["device"],
    )
    session.add(_rule(user.id, device.id, rule_type="threshold", metric="novelty_score"))
    with pytest.raises(sa.exc.IntegrityError):
        await session.commit()
    await session.rollback()


# --- the evaluation path ---------------------------------------------------


async def _score_and_apply(session, user, device, rule, score, now):
    state_row = await session.scalar(
        sa.select(AlertState).where(AlertState.rule_id == rule.id)
    )
    device_row = await session.get(Device, device.id)
    await evaluate_multivariate_pair(
        session, user.id, rule, device_row, state_row, score, now
    )
    await session.commit()


async def test_a_novelty_spike_fires_an_ordinary_alert_event(owned_device):
    """The whole point: no new pipeline. A score past the threshold produces
    the same AlertEvent, through the same state machine, as any other rule."""
    session, user, device = (
        owned_device["session"],
        owned_device["user"],
        owned_device["device"],
    )
    rule = _rule(user.id, device.id)
    session.add(rule)
    await session.commit()

    # Below threshold: no event, and a rule can never fire on its first tick.
    await _score_and_apply(session, user, device, rule, 40.0, NOW)
    assert await session.scalar(
        sa.select(sa.func.count()).select_from(AlertEvent).where(AlertEvent.rule_id == rule.id)
    ) == 0

    # Past threshold twice — PENDING then FIRING, even at for_duration 0.
    await _score_and_apply(session, user, device, rule, 99.0, NOW + timedelta(seconds=15))
    await _score_and_apply(session, user, device, rule, 99.0, NOW + timedelta(seconds=30))

    event = await session.scalar(sa.select(AlertEvent).where(AlertEvent.rule_id == rule.id))
    assert event is not None
    assert event.rule_type == "multivariate"
    assert event.metric == "novelty_score"
    assert event.value_at_fire == 99.0
    assert event.comparison == ">" and event.threshold == 90.0


async def test_no_score_leaves_an_open_alert_alone(owned_device):
    """A device whose model was retrained away, or that went quiet, is
    unknown — not "back to normal". Same rule as Phase 5's "an already-firing
    alert is never auto-resolved by a device going quiet"."""
    session, user, device = (
        owned_device["session"],
        owned_device["user"],
        owned_device["device"],
    )
    rule = _rule(user.id, device.id)
    session.add(rule)
    await session.commit()

    await _score_and_apply(session, user, device, rule, 99.0, NOW)
    await _score_and_apply(session, user, device, rule, 99.0, NOW + timedelta(seconds=15))
    state = await session.scalar(sa.select(AlertState).where(AlertState.rule_id == rule.id))
    assert state.state == "firing"

    await _score_and_apply(session, user, device, rule, None, NOW + timedelta(seconds=30))
    await session.refresh(state)
    assert state.state == "firing"

    event = await session.scalar(sa.select(AlertEvent).where(AlertEvent.rule_id == rule.id))
    assert event.status == "firing"
    assert event.resolved_at is None


async def test_the_score_falling_back_resolves_the_event(owned_device):
    session, user, device = (
        owned_device["session"],
        owned_device["user"],
        owned_device["device"],
    )
    rule = _rule(user.id, device.id)
    session.add(rule)
    await session.commit()

    await _score_and_apply(session, user, device, rule, 99.0, NOW)
    await _score_and_apply(session, user, device, rule, 99.0, NOW + timedelta(seconds=15))
    await _score_and_apply(session, user, device, rule, 20.0, NOW + timedelta(seconds=30))

    event = await session.scalar(sa.select(AlertEvent).where(AlertEvent.rule_id == rule.id))
    assert event.status == "resolved"
    assert event.resolved_value == 20.0


# --- the batched read the sweep uses ---------------------------------------


async def test_score_devices_scores_only_what_it_can(owned_device, model_dir):
    """One query for many devices. A device with no model is absent from the
    result rather than present with a null — the caller uses .get(), so
    absent and unscorable are deliberately indistinguishable."""
    session, user, device = (
        owned_device["session"],
        owned_device["user"],
        owned_device["device"],
    )
    unmodelled = await svc.register_device(session, user_id=user.id, name="mv-box-2")
    _write_model(model_dir, device.id)
    for target in (device, unmodelled):
        session.add(
            MetricSample(
                user_id=user.id,
                device_id=target.id,
                ts=NOW,
                resolution_seconds=1,
                **_reading(),
            )
        )
    await session.commit()

    scores = await novelty_service.score_devices(
        session, [device.id, unmodelled.id], now=NOW
    )

    assert device.id in scores
    assert unmodelled.id not in scores
    assert 0.0 <= scores[device.id] <= 100.0


async def test_score_devices_with_no_models_at_all_makes_no_claims(owned_device, model_dir):
    session, device = owned_device["session"], owned_device["device"]
    assert await novelty_service.score_devices(session, [device.id], now=NOW) == {}
