"""GET /devices/{id}/novelty — the layer-4 score's one surface.

The interesting cases are all the ways there is *no* score. Each is a
different situation with a different fix, so each must arrive as its own
reason string rather than a shared null: "nothing trained on this server",
"nothing trained for this device", "device is quiet", and "this platform
cannot fill the model's vector" are answers a user can act on, and a bare
null is not.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import joblib
import pytest

from app.analysis.multivariate import train
from app.config import get_settings
from app.models import MetricSample, User
from app.security.tokens import issue_access_token
from app.services import enrollment_service as svc
from app.services import novelty_service

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
    """The loader caches by (path, mtime_ns). Two tests writing different
    models to the same tmp path inside one mtime tick would otherwise see
    each other's."""
    novelty_service._CACHE.clear()
    yield
    novelty_service._CACHE.clear()


@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    # get_settings() is lru_cached, so this patches the one shared Settings
    # instance in place and monkeypatch restores it at teardown.
    monkeypatch.setattr(get_settings(), "novelty_model_dir", str(tmp_path), raising=False)
    return tmp_path


@pytest.fixture
async def device_with_reading(admin_session):
    user = User(email=f"novelty-{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="novelty-box")

    async def add_reading(ts=None, **overrides):
        admin_session.add(
            MetricSample(
                user_id=user.id,
                device_id=device.id,
                ts=ts or NOW,
                resolution_seconds=1,
                **_reading(**overrides),
            )
        )
        await admin_session.commit()

    return {"user": user, "device": device, "add_reading": add_reading}


def _write_model(directory, device_id, rows=None):
    import numpy as np

    rng = np.random.default_rng(3)
    rows = rows or [
        _reading(
            cpu_percent=float(abs(rng.normal(8, 2))),
            mem_percent=float(rng.normal(55, 3)),
            load1=float(abs(rng.normal(1.5, 0.4))),
        )
        for _ in range(400)
    ]
    model = train(rows)
    assert model is not None
    joblib.dump(model, directory / f"{device_id}.joblib")
    return model


async def test_no_model_dir_configured_is_explained_not_a_null(
    client, device_with_reading, monkeypatch
):
    monkeypatch.setattr(get_settings(), "novelty_model_dir", None, raising=False)
    user, device = device_with_reading["user"], device_with_reading["device"]

    r = await client.get(f"/devices/{device.id}/novelty", headers=_headers(user.id))

    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["score"] is None
    assert "trained" in body["reason"]


async def test_a_device_with_no_model_of_its_own_says_so(client, device_with_reading, model_dir):
    user, device = device_with_reading["user"], device_with_reading["device"]

    r = await client.get(f"/devices/{device.id}/novelty", headers=_headers(user.id))

    assert r.status_code == 200
    assert r.json()["available"] is False
    assert "history" in r.json()["reason"]


async def test_a_quiet_device_is_not_scored_on_a_stale_reading(
    client, device_with_reading, model_dir
):
    """A reading older than the 90s freshness window is not "current" — the
    same rule every headline number on a summary follows."""
    user, device = device_with_reading["user"], device_with_reading["device"]
    _write_model(model_dir, device.id)
    await device_with_reading["add_reading"](ts=NOW - timedelta(minutes=10))

    r = await client.get(f"/devices/{device.id}/novelty", headers=_headers(user.id))

    assert r.json()["available"] is False
    assert "90 seconds" in r.json()["reason"]


async def test_a_fresh_reading_against_a_real_model_scores(client, device_with_reading, model_dir):
    user, device = device_with_reading["user"], device_with_reading["device"]
    _write_model(model_dir, device.id)
    await device_with_reading["add_reading"]()

    r = await client.get(f"/devices/{device.id}/novelty", headers=_headers(user.id))

    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert 0.0 <= body["score"] <= 100.0
    assert body["trained_on_samples"] == 400
    assert "cpu_percent" in body["feature_names"]
    assert body["reading_ts"] is not None


async def test_an_unusual_reading_scores_higher_than_an_ordinary_one(
    client, device_with_reading, model_dir
):
    """The end-to-end version of the claim the whole layer rests on."""
    user, device = device_with_reading["user"], device_with_reading["device"]
    _write_model(model_dir, device.id)

    await device_with_reading["add_reading"](ts=NOW - timedelta(seconds=30))
    ordinary = (
        await client.get(f"/devices/{device.id}/novelty", headers=_headers(user.id))
    ).json()["score"]

    await device_with_reading["add_reading"](ts=NOW, cpu_percent=99.0, load1=48.0, mem_percent=97.0)
    unusual = (await client.get(f"/devices/{device.id}/novelty", headers=_headers(user.id))).json()[
        "score"
    ]

    assert unusual > ordinary


async def test_a_device_that_cannot_fill_the_vector_names_what_is_missing(
    client, device_with_reading, model_dir
):
    """An Android device reports no load1 or CPU. The answer is which fields
    are absent, not a fabricated score — docs/ANDROID_METRICS.md's rule
    reaching a feature written long after it."""
    user, device = device_with_reading["user"], device_with_reading["device"]
    _write_model(model_dir, device.id)
    await device_with_reading["add_reading"](load1=None, cpu_percent=None)

    r = await client.get(f"/devices/{device.id}/novelty", headers=_headers(user.id))

    body = r.json()
    assert body["available"] is False
    assert "load1" in body["reason"] and "cpu_percent" in body["reason"]


async def test_another_users_device_404s(client, device_with_reading, model_dir):
    device = device_with_reading["device"]
    stranger = User(email=f"novelty-x-{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    from app.db import AdminSessionLocal

    async with AdminSessionLocal() as session:
        session.add(stranger)
        await session.commit()

    r = await client.get(f"/devices/{device.id}/novelty", headers=_headers(stranger.id))
    assert r.status_code == 404
