"""app/analysis/multivariate.py: the trained layer-4 novelty model.

Pure — rows are plain dicts, no DB and no fixtures. The tests are mostly
about the two rules that are this codebase's rather than scikit-learn's: a
row with a missing feature is dropped rather than imputed, and too little
data yields no model rather than a bad one.

Determinism note: IsolationForest is a randomised algorithm, so every test
here pins `random_state`. A test that passed only on sklearn's default seed
would be asserting a coincidence.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.analysis.multivariate import (
    FEATURES,
    MIN_TRAINING_SAMPLES,
    build_matrix,
    feature_vector,
    novelty_percentile,
    score_row,
    train,
)


def _row(**overrides) -> dict:
    """An ordinary idle-desktop reading; overrides make it unusual."""
    base = {
        "cpu_percent": 8.0,
        "cpu_user_percent": 5.0,
        "cpu_system_percent": 3.0,
        "mem_percent": 55.0,
        "swap_percent": 0.0,
        "load1": 1.5,
        "process_count": 420.0,
    }
    base.update(overrides)
    return base


def _ordinary_history(n: int = 600, seed: int = 1) -> list[dict]:
    """n rows of plausible idle-machine jitter around _row()'s values."""
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        cpu_user = float(abs(rng.normal(5.0, 1.5)))
        cpu_sys = float(abs(rng.normal(3.0, 1.0)))
        rows.append(
            _row(
                cpu_percent=cpu_user + cpu_sys,
                cpu_user_percent=cpu_user,
                cpu_system_percent=cpu_sys,
                mem_percent=float(rng.normal(55.0, 3.0)),
                load1=float(abs(rng.normal(1.5, 0.4))),
                process_count=float(rng.integers(400, 440)),
            )
        )
    return rows


# --- the vector ------------------------------------------------------------


def test_a_complete_row_becomes_a_vector_in_feature_order():
    assert feature_vector(_row()) == [8.0, 5.0, 3.0, 55.0, 0.0, 1.5, 420.0]


def test_a_missing_feature_yields_no_vector_rather_than_a_zero():
    """The no-imputation rule. A zero here would teach the model that the
    fabricated value is normal, and it would then never flag it."""
    row = _row()
    del row["load1"]
    assert feature_vector(row) is None
    assert feature_vector(_row(load1=None)) is None


def test_a_non_finite_reading_is_dropped_too():
    """sklearn raises on NaN at fit time, naming the whole matrix rather than
    the row that caused it — cheaper to refuse it here."""
    assert feature_vector(_row(cpu_percent=float("nan"))) is None
    assert feature_vector(_row(load1=math.inf)) is None


def test_build_matrix_drops_incomplete_rows_and_keeps_the_rest():
    rows = [_row(), _row(load1=None), _row(mem_percent=60.0)]
    matrix = build_matrix(rows)
    assert matrix.shape == (2, len(FEATURES))


def test_build_matrix_of_nothing_still_has_the_right_width():
    """An empty (0, n) array, not an empty list — so a caller can check
    .shape[0] without special-casing."""
    assert build_matrix([]).shape == (0, len(FEATURES))
    assert build_matrix([{"cpu_percent": 1.0}]).shape == (0, len(FEATURES))


# --- training --------------------------------------------------------------


def test_too_little_history_trains_no_model_at_all():
    """Same posture as anomaly.py's WARMUP_SAMPLES: unknown, not guessed."""
    assert train(_ordinary_history(MIN_TRAINING_SAMPLES - 1)) is None


def test_enough_history_trains_a_model_that_reports_what_it_learned_from():
    model = train(_ordinary_history(400))
    assert model is not None
    assert model.sample_count == 400
    assert model.feature_names == FEATURES
    assert model.n_features == len(FEATURES)


def test_incomplete_rows_do_not_count_toward_the_minimum():
    """300 rows of which 250 are unusable is 50 rows of training data, and
    must be treated as such rather than as 300."""
    rows = _ordinary_history(50) + [_row(load1=None) for _ in range(250)]
    assert train(rows, min_samples=100) is None
    assert train(rows, min_samples=50) is not None


def test_the_stored_score_distribution_is_ascending():
    """novelty_percentile() searchsorts against it, which silently returns
    nonsense on an unsorted array."""
    model = train(_ordinary_history(400))
    assert model is not None
    quantiles = list(model.score_quantiles)
    assert quantiles == sorted(quantiles)


# --- scoring ---------------------------------------------------------------


def test_an_ordinary_reading_scores_low_and_a_wild_one_scores_high():
    """The whole point of the layer, in one assertion."""
    model = train(_ordinary_history(600))
    assert model is not None

    ordinary = score_row(model, _row())
    wild = score_row(model, _row(cpu_percent=99.0, load1=64.0, mem_percent=99.0))

    assert ordinary is not None and wild is not None
    assert ordinary < 50.0
    assert wild > 90.0
    assert wild > ordinary


def test_it_catches_a_combination_that_no_single_metric_would_flag():
    """This is what layer 4 exists for and what Phase 6's univariate
    baselines cannot do: every value below is individually inside the
    training range, and only their *combination* never occurs — high CPU
    while the load average stays at idle."""
    model = train(_ordinary_history(600))
    assert model is not None

    # cpu_percent ~13 and load1 ~1.5 are both ordinary on their own; 13% CPU
    # is near the top of the training range and 1.5 is dead centre.
    combination = score_row(
        model,
        _row(cpu_percent=13.0, cpu_user_percent=11.0, cpu_system_percent=2.0, load1=0.05),
    )
    baseline = score_row(model, _row())
    assert combination is not None and baseline is not None
    assert combination > baseline


def test_a_reading_missing_a_feature_scores_none_rather_than_guessing():
    model = train(_ordinary_history(400))
    assert model is not None
    assert score_row(model, _row(swap_percent=None)) is None


def test_novelty_is_bounded_to_0_100():
    model = train(_ordinary_history(400))
    assert model is not None
    for row in (_row(), _row(cpu_percent=1e6, load1=1e6), _row(cpu_percent=0.0, load1=0.0)):
        score = score_row(model, row)
        assert score is not None
        assert 0.0 <= score <= 100.0


def test_a_wrong_length_vector_is_refused_rather_than_scored():
    model = train(_ordinary_history(400))
    assert model is not None
    with pytest.raises(ValueError, match="expected 7 features"):
        novelty_percentile(model, [1.0, 2.0])


def test_training_is_reproducible_for_a_fixed_seed():
    """A retrain on unchanged data must not move a device's scores, or every
    sweep would look like drift."""
    rows = _ordinary_history(400)
    a, b = train(rows, random_state=7), train(rows, random_state=7)
    assert a is not None and b is not None
    assert score_row(a, _row()) == score_row(b, _row())
