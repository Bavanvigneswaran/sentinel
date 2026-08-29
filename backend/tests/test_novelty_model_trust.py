"""`joblib.load` is pickle, and unpickling executes arbitrary code — in the API
process, which holds the database credential and the JWT signing key.

That is acceptable because these files are operator-written: never uploaded,
never user-supplied, never fetched. The module has always said so. What it did
not do was *check*, so the precondition held only as long as nobody's
permissions drifted. See app/services/novelty_service.py.
"""

import os
import uuid

import joblib
import pytest

from app.analysis.multivariate import TrainedModel
from app.services import model_integrity, novelty_service


@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    """A model directory with one real, correctly-permissioned model in it."""
    from app.config import Settings

    configured = Settings(_env_file=None, environment="test", novelty_model_dir=str(tmp_path))
    monkeypatch.setattr(novelty_service, "get_settings", lambda: configured)
    novelty_service._CACHE.clear()
    return tmp_path


def _write_model(directory, device_id) -> None:
    import numpy as np
    from sklearn.ensemble import IsolationForest

    rows = np.random.default_rng(0).normal(size=(200, 3))
    forest = IsolationForest(n_estimators=10, random_state=0).fit(rows)
    model = TrainedModel(
        estimator=forest,
        feature_names=("cpu_percent", "mem_percent", "disk_percent"),
        sample_count=200,
        score_quantiles=tuple(float(x) for x in np.linspace(-0.6, -0.4, 101)),
    )
    path = directory / f"{device_id}.joblib"
    joblib.dump(model, path)
    # Signed, because an unsigned model is now refused at load — see
    # app/services/model_integrity.py and tests/test_model_integrity.py.
    model_integrity.sign(path)


def test_a_correctly_permissioned_model_loads(model_dir):
    device_id = uuid.uuid4()
    _write_model(model_dir, device_id)
    os.chmod(model_dir, 0o755)
    os.chmod(model_dir / f"{device_id}.joblib", 0o644)

    assert novelty_service.load_model(device_id) is not None


@pytest.mark.skipif(os.name != "posix", reason="st_mode says nothing useful on Windows")
def test_a_world_writable_model_is_refused(model_dir):
    """At this point "operator-written" has stopped being true, and the honest
    answer is no score rather than unpickling whatever that account wrote."""
    device_id = uuid.uuid4()
    _write_model(model_dir, device_id)
    os.chmod(model_dir / f"{device_id}.joblib", 0o666)
    novelty_service._CACHE.clear()

    assert novelty_service.load_model(device_id) is None


@pytest.mark.skipif(os.name != "posix", reason="st_mode says nothing useful on Windows")
def test_a_world_writable_directory_is_refused(model_dir):
    """The file's own mode is not enough: anyone who can write the directory can
    replace the file."""
    device_id = uuid.uuid4()
    _write_model(model_dir, device_id)
    os.chmod(model_dir / f"{device_id}.joblib", 0o644)
    os.chmod(model_dir, 0o777)
    novelty_service._CACHE.clear()
    try:
        assert novelty_service.load_model(device_id) is None
    finally:
        os.chmod(model_dir, 0o755)


@pytest.mark.skipif(os.name != "posix", reason="st_mode says nothing useful on Windows")
def test_group_writable_counts_too(model_dir):
    device_id = uuid.uuid4()
    _write_model(model_dir, device_id)
    os.chmod(model_dir / f"{device_id}.joblib", 0o664)
    novelty_service._CACHE.clear()

    assert novelty_service.load_model(device_id) is None


def test_a_missing_model_is_still_just_none(model_dir):
    assert novelty_service.load_model(uuid.uuid4()) is None
