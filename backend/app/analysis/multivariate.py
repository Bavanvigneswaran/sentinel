"""Layer 4 of the detection stack: a trained multivariate novelty model over
the joint system-metric vector.

docs/ARCHITECTURE.md has named this since Phase 0 ("nightly-retrained
IsolationForest over the joint metric vector. Catches correlated weirdness no
single threshold sees") and it was never built. This is that layer.

What it adds over Phase 6, which is the only reason it earns its place: the
EWMA/MAD baseline in analysis/anomaly.py is *univariate*, judging one metric
against its own history. It cannot see a combination. An IsolationForest over
the whole vector can say "40% CPU is ordinary here, 60% memory is ordinary
here, and the two together with this load average has never happened on this
machine."

Like every other module in analysis/, this is pure: no I/O, no DB, no clock.
Training data comes in as plain mappings and a trained model goes out as a
dataclass; who reads the rows and where the model is persisted are somebody
else's problem.

Two things follow this codebase's existing rules rather than scikit-learn's
conventions, and both are deliberate:

* **A row with any feature missing is dropped, never imputed.** Filling a NULL
  with a zero or a column mean is precisely the "synthesise a metric" that
  CLAUDE.md forbids, and it would be worse here than on a chart: the model
  would learn that the fabricated value is normal, and then not flag it.
* **Too little data means no model, not a bad one.** `train()` returns None
  below MIN_TRAINING_SAMPLES, the same "unknown, not synthesised" posture as
  anomaly.py's WARMUP_SAMPLES and forecast.py's MIN_POINTS_TREND.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest

#: The joint vector. Every one of these lives in the *same* rollup domain
#: (metric_samples), so building a training set is one query rather than a
#: join across the disk/net/latency domains — and, more importantly, they are
#: the columns measured on every desktop platform.
#:
#: Deliberately excluded despite being in the same table and looking useful:
#: `ctx_switches_per_s` and `active_connections` are 100% populated on Windows
#: and 0% on macOS (psutil's net_connections() needs root on macOS — Phase 15;
#: ctx_switches is a per-interval delta there — Phase 2). Including either
#: would mean every macOS row is dropped by the no-imputation rule above, i.e.
#: no model at all on the platform this project is developed on. Measured
#: before choosing, not assumed.
FEATURES: tuple[str, ...] = (
    "cpu_percent",
    "cpu_user_percent",
    "cpu_system_percent",
    "mem_percent",
    "swap_percent",
    "load1",
    "process_count",
)

#: Below this many complete rows, train() returns None. At the 1m rollup this
#: is a little over three hours of continuous reporting — enough for an
#: IsolationForest over 7 features to have seen a machine idle and busy,
#: without demanding the multiple days a seasonal model would.
MIN_TRAINING_SAMPLES = 200

#: Trees in the forest. sklearn's default; raised only if the score
#: distribution turns out unstable between retrains on real data.
N_ESTIMATORS = 100

#: "auto" uses the original Isolation Forest paper's offset rather than
#: assuming a fixed fraction of the training data is anomalous. That matters
#: here because the training set is a machine's *ordinary* history: asserting
#: up front that (say) 5% of it was anomalous would manufacture a positive
#: rate out of nothing. Novelty is reported as a percentile against the
#: training distribution instead — see novelty_percentile().
CONTAMINATION = "auto"

#: Resolution of the stored score distribution: 1001 points is every 0.1th
#: percentile. The anomalous tail is the end that matters, so 1% buckets
#: would be too coarse exactly where the model is used.
QUANTILE_POINTS = 1001

RANDOM_STATE = 0


@dataclass(frozen=True)
class TrainedModel:
    """A fitted forest plus what is needed to interpret its output.

    `score_quantiles` is the load-bearing extra. sklearn's `score_samples()`
    returns an unbounded, dataset-specific float that means nothing on its
    own and is not comparable between two machines. Storing the training
    distribution lets a raw score become "more unusual than 99.4% of this
    machine's own history", which is both explainable to a user and stable
    across retrains.
    """

    estimator: IsolationForest
    feature_names: tuple[str, ...]
    sample_count: int
    #: Ascending. Lower score = more anomalous, per sklearn's convention.
    score_quantiles: tuple[float, ...]

    @property
    def n_features(self) -> int:
        return len(self.feature_names)


def feature_vector(
    row: Mapping[str, Any], features: tuple[str, ...] = FEATURES
) -> list[float] | None:
    """One row's vector, or None when the row cannot fill it.

    None is returned for a missing key and for an explicit None value alike:
    both mean "this platform did not measure it", and neither is a number.
    Callers drop the row rather than patching it — see the module docstring.
    """
    vector: list[float] = []
    for name in features:
        value = row.get(name)
        if value is None:
            return None
        try:
            as_float = float(value)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(as_float):
            # A NaN or inf reaching the forest poisons every split it lands
            # in, and sklearn raises on it at fit time rather than at the row
            # that caused it, which makes the real culprit hard to find.
            return None
        vector.append(as_float)
    return vector


def build_matrix(
    rows: Iterable[Mapping[str, Any]], features: tuple[str, ...] = FEATURES
) -> np.ndarray:
    """Stack whichever rows can fill the vector into an (n, len(features))
    array. Rows that cannot are silently dropped — that is the intended
    behaviour, and `sample_count` on the trained model reports how many
    survived so the drop is visible rather than implied."""
    vectors = [v for row in rows if (v := feature_vector(row, features)) is not None]
    if not vectors:
        return np.empty((0, len(features)), dtype=float)
    return np.asarray(vectors, dtype=float)


def train(
    rows: Iterable[Mapping[str, Any]],
    *,
    features: tuple[str, ...] = FEATURES,
    min_samples: int = MIN_TRAINING_SAMPLES,
    random_state: int = RANDOM_STATE,
) -> TrainedModel | None:
    """Fit an IsolationForest on a device's own ordinary history.

    Returns None — never a model fitted on too little data — when fewer than
    `min_samples` rows could fill the vector.

    No feature scaling, and that is not an oversight: an IsolationForest
    splits at a uniformly random threshold *within each feature's own
    observed range*, so any monotone per-feature rescaling produces the same
    partitions. A StandardScaler here would add a second fitted object to
    persist and version for no change in output.
    """
    matrix = build_matrix(rows, features)
    if matrix.shape[0] < min_samples:
        return None

    estimator = IsolationForest(
        n_estimators=N_ESTIMATORS,
        contamination=CONTAMINATION,
        random_state=random_state,
    )
    estimator.fit(matrix)

    # The training set's own score distribution, which is what turns an
    # opaque float into a percentile at inference time.
    training_scores = estimator.score_samples(matrix)
    quantiles = np.quantile(training_scores, np.linspace(0.0, 1.0, QUANTILE_POINTS))

    return TrainedModel(
        estimator=estimator,
        feature_names=tuple(features),
        sample_count=int(matrix.shape[0]),
        score_quantiles=tuple(float(q) for q in quantiles),
    )


def novelty_percentile(model: TrainedModel, vector: list[float]) -> float:
    """How unusual this vector is, 0-100, against the model's own training
    history. 0 means "as ordinary as this machine gets"; 100 means "stranger
    than anything in the training window".

    A percentile rather than the raw score because the raw score is
    unbounded, dataset-specific, and not comparable between machines — see
    TrainedModel.score_quantiles.
    """
    if len(vector) != model.n_features:
        raise ValueError(
            f"expected {model.n_features} features {model.feature_names}, got {len(vector)}"
        )
    raw = float(model.estimator.score_samples([vector])[0])
    quantiles = np.asarray(model.score_quantiles)
    # Ascending quantiles, and a lower score is more anomalous, so the
    # fraction of training history scoring *below* this vector is how
    # ordinary it is; novelty is the complement.
    position = float(np.searchsorted(quantiles, raw)) / (len(quantiles) - 1)
    ordinariness = min(max(position, 0.0), 1.0)
    return round((1.0 - ordinariness) * 100.0, 2)


def score_row(model: TrainedModel, row: Mapping[str, Any]) -> float | None:
    """novelty_percentile() for a raw reading, or None when the reading
    cannot fill the model's vector — the same "no answer" rather than a
    fabricated one that feature_vector() returns."""
    vector = feature_vector(row, model.feature_names)
    if vector is None:
        return None
    return novelty_percentile(model, vector)
