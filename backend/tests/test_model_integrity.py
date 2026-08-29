"""Provenance on the one file this product unpickles.

`joblib.load` executes arbitrary code during load, in the process that holds
the database credential and the JWT signing key. The permission check in
`novelty_service.py` narrows *who* can write a model; the HMAC sidecar narrows
it to whoever holds this deployment's secret, which is the property that
actually stops an attacker who got a write.
"""

from __future__ import annotations

import uuid

import joblib
import pytest

from app.services import model_integrity, novelty_service


@pytest.fixture
def signed_model(tmp_path, monkeypatch):
    """A real model on disk, correctly signed, with the service pointed at it."""
    import numpy as np
    from sklearn.ensemble import IsolationForest

    from app.analysis.multivariate import TrainedModel
    from app.config import Settings

    configured = Settings(_env_file=None, environment="test", novelty_model_dir=str(tmp_path))
    monkeypatch.setattr(novelty_service, "get_settings", lambda: configured)
    novelty_service._CACHE.clear()

    device_id = uuid.uuid4()
    rows = np.random.default_rng(0).normal(size=(200, 3))
    model = TrainedModel(
        estimator=IsolationForest(n_estimators=10, random_state=0).fit(rows),
        feature_names=("cpu_percent", "mem_percent", "disk_percent"),
        sample_count=200,
        score_quantiles=tuple(float(x) for x in np.linspace(-0.6, -0.4, 101)),
    )
    path = tmp_path / f"{device_id}.joblib"
    joblib.dump(model, path)
    model_integrity.sign(path)
    return device_id, path


def test_a_signed_model_loads(signed_model):
    device_id, _ = signed_model
    assert novelty_service.load_model(device_id) is not None


def test_a_tampered_model_is_refused(signed_model):
    """The case the whole file exists for: an attacker who got a write, by a
    permissions drift or a restored backup, must not get an unpickle."""
    device_id, path = signed_model
    path.write_bytes(path.read_bytes() + b"tampered")
    novelty_service._CACHE.clear()

    assert novelty_service.load_model(device_id) is None


def test_a_model_with_no_signature_is_refused(signed_model):
    """A missing sidecar is a failure, not a pass.

    Treating it as "unsigned, so allow" would mean an attacker deletes one file
    to bypass the check, which is how signature schemes are usually defeated in
    practice rather than by forging anything."""
    device_id, path = signed_model
    model_integrity.signature_path(path).unlink()
    novelty_service._CACHE.clear()

    assert novelty_service.load_model(device_id) is None


def test_a_signature_from_a_different_secret_is_refused(signed_model, monkeypatch):
    """A model carried over from another deployment does not authenticate here."""
    device_id, path = signed_model
    from app.config import Settings

    other = Settings(_env_file=None, environment="test", jwt_secret="a-different-deployments-key")
    monkeypatch.setattr(model_integrity, "get_settings", lambda: other)
    model_integrity.sign(path)
    monkeypatch.undo()
    novelty_service._CACHE.clear()

    assert novelty_service.load_model(device_id) is None


def test_the_signing_key_is_not_the_jwt_secret_itself(monkeypatch):
    """Derived, not reused. Signing a model and signing a token are different
    purposes and must not share a key by accident."""
    from app.config import Settings

    configured = Settings(_env_file=None, environment="test", jwt_secret="x" * 48)
    monkeypatch.setattr(model_integrity, "get_settings", lambda: configured)
    assert model_integrity._key() != configured.jwt_secret.encode()


def test_the_sidecar_sits_beside_the_model(tmp_path):
    path = tmp_path / "abc.joblib"
    assert model_integrity.signature_path(path).name == "abc.joblib.sig"
