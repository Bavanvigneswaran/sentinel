"""Adaptive per-(device, metric) baseline: EWMA mean + MAD-based spread, and
the z-score/sensitivity/severity math built on top of it.

No I/O, no DB — same style as analysis/alerts.py and analysis/health.py. The
evaluator owns reading/persisting `BaselineState`; this module only knows how
to fold one new value in and how to judge one value against a baseline.

Constants, and why:

* `EWMA_ALPHA = 0.1` — effective averaging window is ~(2-alpha)/alpha ≈ 19
  samples. At the evaluator's tick cadence this lets a genuine baseline shift
  (a new steady-state load) get absorbed within roughly 20 ticks, while a
  single noisy tick only moves the mean by 10% of the gap — slow enough not
  to be an accomplice in swallowing the very anomaly it's supposed to flag.
* `MAD_SCALE = 1.4826` — the standard median-absolute-deviation-to-sigma
  conversion (1 / Phi^-1(0.75)), derived for a *median* absolute deviation.
  `BaselineState.mad` is actually an EWMA of the absolute deviation (an O(1)
  streaming approximation — a true running median needs unbounded history),
  for which the textbook-correct constant is 1/sqrt(2/pi) ≈ 1.2533. 1.4826 is
  used anyway per an explicit design decision; noted here so the discrepancy
  isn't mistaken for a bug. A one-constant change if reconsidered after real
  tuning.
* `MIN_SPREAD = 0.5` — every alertable metric (see models/alerts.py METRICS)
  is a percentage. Below half a percentage point of spread, tightening
  further only manufactures z-score sensitivity out of measurement noise,
  not signal, and prevents division-by-zero on a perfectly flat metric (e.g.
  swap_percent pinned at 0.0 on a machine with no swap).
* `WARMUP_SAMPLES = 20` — matches the EWMA's own effective window: before
  ~20 samples the mean/mad estimate hasn't converged enough to trust. Treating
  a still-forming baseline as "unknown" is the direct analogue of
  analysis/alerts.py's step() treating a missing reading as "unknown" rather
  than synthesising an answer.
* `SENSITIVITY_CUTOFFS` — for a roughly Gaussian metric, z>=2 is about the
  top/bottom 2.3% of samples (frequent — "high" sensitivity, more false
  positives tolerated), z>=3 is about 0.13% (the conventional three-sigma
  rule of thumb — "medium"), z>=4 is about 0.003% (rare — "low", only
  genuinely extreme deviations page).
* `SEVERITY_CUTOFFS` — independent of the firing cutoff above (which depends
  on sensitivity): severity classifies *how far* into anomaly territory an
  already-firing event is, for the UI badge, not a second trigger threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Sensitivity = Literal["low", "medium", "high"]
Severity = Literal["watch", "warning", "critical"]

EWMA_ALPHA = 0.1
MAD_SCALE = 1.4826
MIN_SPREAD = 0.5
WARMUP_SAMPLES = 20

SENSITIVITY_CUTOFFS: dict[Sensitivity, float] = {"low": 4.0, "medium": 3.0, "high": 2.0}
#: Ordered highest-cutoff-first; the first threshold met wins. Below the
#: lowest cutoff, classify_severity() returns "watch".
SEVERITY_CUTOFFS: tuple[tuple[float, Severity], ...] = ((6.0, "critical"), (4.0, "warning"))


@dataclass(frozen=True)
class BaselineState:
    mean: float
    #: EWMA of the absolute deviation from mean, unscaled. Callers that need
    #: a sigma-equivalent spread go through scaled_spread() rather than
    #: re-deriving MAD_SCALE themselves.
    mad: float
    sample_count: int


def update_baseline(
    state: BaselineState | None, value: float, *, alpha: float = EWMA_ALPHA
) -> BaselineState:
    """Fold one new observation into the baseline. `state=None` seeds a fresh
    baseline from this single value (mad=0, sample_count=1)."""
    if state is None:
        return BaselineState(mean=value, mad=0.0, sample_count=1)
    new_mean = state.mean + alpha * (value - state.mean)
    new_mad = state.mad + alpha * (abs(value - new_mean) - state.mad)
    return BaselineState(mean=new_mean, mad=new_mad, sample_count=state.sample_count + 1)


def scaled_spread(state: BaselineState) -> float:
    return max(state.mad * MAD_SCALE, MIN_SPREAD)


def z_score(value: float, state: BaselineState) -> float:
    return (value - state.mean) / scaled_spread(state)


def cutoff_for_sensitivity(sensitivity: Sensitivity) -> float:
    return SENSITIVITY_CUTOFFS[sensitivity]


def is_anomalous(z: float, sensitivity: Sensitivity) -> bool:
    return abs(z) >= cutoff_for_sensitivity(sensitivity)


def classify_severity(z: float) -> Severity:
    magnitude = abs(z)
    for cutoff, severity in SEVERITY_CUTOFFS:
        if magnitude >= cutoff:
            return severity
    return "watch"
