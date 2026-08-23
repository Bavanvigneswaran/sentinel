"""24h Holt-Winters forecasts and disk/memory time-to-exhaustion.

No I/O, no DB — same style as analysis/anomaly.py and analysis/health.py. The
forecast worker (app/workers/forecast_worker.py) owns fetching history and
persisting the result; this module only knows how to turn a plain list of
real observations into a forecast or an exhaustion estimate.

Constants, and why:

* `MIN_POINTS_TREND = 24` — below roughly a day's worth of hourly-ish
  buckets, a trend fit is mostly extrapolating noise. Below this, return
  `None` rather than a number nobody should trust — the same "unknown, not
  synthesized" posture analysis/anomaly.py's `WARMUP_SAMPLES` uses for a
  still-forming baseline.
* `MIN_POINTS_SEASONAL` / `SEASONAL_PERIOD` — statsmodels' ETS needs at
  least two full seasonal cycles to fit a seasonal component at all,  so
  seasonality (a daily cycle, `SEASONAL_PERIOD = 24` buckets) is only
  attempted once there are two full days of history; below that the fit
  falls back to trend-only, which still needs `MIN_POINTS_TREND`.
* `PREDICTION_INTERVAL_ALPHA = 0.1` — a 90% interval. Wide enough to be
  honest about how little a household box's infrastructure metrics are
  actually seasonal in the first couple of weeks (see
  docs/ARCHITECTURE.md's "known constraints"), narrow enough to still be
  informative on a chart.
* `MAX_FORECAST_STEPS` — a safety cap on how many points a forecast can ever
  return, independent of the caller's bucket width. Exists so a caller
  passing an unexpectedly fine bucket cannot turn one fit into an
  unbounded-size response.
* `MIN_POINTS_EXHAUSTION = 8` — far more forgiving than the trend forecast's
  warmup: a Theil-Sen slope is a much simpler, more robust statistic than an
  ETS fit and is meaningful with far less history.
* `EXHAUSTION_MAX_HORIZON_DAYS` — a projection decades out is not a
  forecast, it is arithmetic noise dressed up as one. Beyond this horizon,
  `projected_at` is `None` — indistinguishable from "not trending upward at
  all", which is the honest thing to tell a user in both cases: neither is
  actionable.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy.stats import theilslopes
from statsmodels.tsa.exponential_smoothing.ets import ETSModel

HORIZON_SECONDS = 86_400
PREDICTION_INTERVAL_ALPHA = 0.1
MIN_POINTS_TREND = 24
MIN_POINTS_SEASONAL = 48
SEASONAL_PERIOD = 24
MAX_FORECAST_STEPS = 500

MIN_POINTS_EXHAUSTION = 8
EXHAUSTION_MAX_HORIZON_DAYS = 3650.0

#: A forecast never projects further ahead than the span it was fitted on.
#: This is what makes an *early* forecast honest rather than reckless: the
#: worker now plans its window from when a device actually started reporting,
#: so a four-minute-old device gets 10s buckets and enough points to fit — but
#: projecting that 24 hours ahead would be exactly the "arithmetic noise
#: dressed up as a forecast" this module exists to refuse. The horizon grows
#: with the history instead: four minutes of data forecasts four minutes
#: ahead, and only once there is a full day does the 24h default apply.
MAX_HORIZON_RATIO = 1.0

#: History spans below which a forecast is labelled provisional. Purely a
#: presentation concern — the numbers are identical either way; what changes is
#: whether the UI invites anyone to act on them.
CONFIDENCE_HIGH_SECONDS = 2 * 86_400
CONFIDENCE_MEDIUM_SECONDS = 6 * 3_600


def forecast_confidence(history_seconds: float) -> str:
    """How much a forecast built on `history_seconds` of data deserves trust.

    Derived at read time from the fit's own inputs rather than stored, the same
    way Phase 6's alert severity is a computed field over `z_score` instead of
    a second copy that can drift.
    """
    if history_seconds >= CONFIDENCE_HIGH_SECONDS:
        return "high"
    if history_seconds >= CONFIDENCE_MEDIUM_SECONDS:
        return "medium"
    return "provisional"



@dataclass(frozen=True)
class ForecastPoint:
    #: Seconds after the last observed sample.
    offset_seconds: int
    predicted: float
    lower: float
    upper: float


def fit_holt_winters(
    values: list[float], bucket_seconds: int, *, horizon_seconds: int = HORIZON_SECONDS
) -> tuple[ForecastPoint, ...] | None:
    """A 24h-default forecast with a 90% prediction interval, or `None` when
    there isn't enough real history to trust one.

    `values` must already have any gap dropped by the caller — this function
    treats the list as a contiguous, evenly-spaced series and has no notion
    of a missing bucket.
    """
    if len(values) < MIN_POINTS_TREND or bucket_seconds <= 0:
        return None

    # Never project further ahead than the history behind the fit. Without
    # this, a device four minutes old — which now gets a fit at all, because
    # the worker plans its window from when the device started reporting —
    # would extrapolate a full day from 24 ten-second buckets.
    observed_seconds = len(values) * bucket_seconds
    horizon_seconds = min(horizon_seconds, int(observed_seconds * MAX_HORIZON_RATIO))
    steps = min(MAX_FORECAST_STEPS, max(1, horizon_seconds // bucket_seconds))
    endog = pd.Series(np.asarray(values, dtype=float), index=pd.RangeIndex(len(values)))

    seasonal = len(values) >= MIN_POINTS_SEASONAL
    try:
        with warnings.catch_warnings():
            # A flat or still-settling metric routinely fails to fully
            # converge; the fit is still usable, and logging a warning every
            # worker tick for every such metric would be pure noise.
            warnings.simplefilter("ignore")
            model = ETSModel(
                endog,
                error="add",
                trend="add",
                damped_trend=True,
                seasonal="add" if seasonal else None,
                seasonal_periods=SEASONAL_PERIOD if seasonal else None,
            )
            fit = model.fit(disp=False)
            prediction = fit.get_prediction(start=len(values), end=len(values) + steps - 1)
            frame = prediction.summary_frame(alpha=PREDICTION_INTERVAL_ALPHA)
    except Exception:  # noqa: BLE001 — any fit failure means "no forecast", not a crash
        return None

    return tuple(
        ForecastPoint(
            offset_seconds=(i + 1) * bucket_seconds,
            predicted=float(row["mean"]),
            lower=float(row["pi_lower"]),
            upper=float(row["pi_upper"]),
        )
        for i, (_, row) in enumerate(frame.iterrows())
    )


@dataclass(frozen=True)
class ExhaustionEstimate:
    slope_per_day: float
    #: None means "not projected to reach the ceiling on any actionable
    #: horizon" — either the trend isn't upward, or it is but so slowly that
    #: the projection exceeds EXHAUSTION_MAX_HORIZON_DAYS. The two are
    #: indistinguishable to a user and the field name says so either way.
    projected_at: datetime | None


def estimate_time_to_exhaustion(
    timestamps: list[datetime],
    values: list[float],
    *,
    ceiling: float = 100.0,
    now: datetime,
) -> ExhaustionEstimate | None:
    """Robust linear projection of when `values` reaches `ceiling`.

    The projection is anchored at the last *real* observed value, never at
    the regression line's own fitted value for "now" — the starting point of
    a projection has to be something that was actually measured.
    """
    if len(values) < MIN_POINTS_EXHAUSTION:
        return None

    t0 = timestamps[0]
    elapsed_days = np.array([(t - t0).total_seconds() / 86_400 for t in timestamps])
    slope, _intercept, _low, _high = theilslopes(np.asarray(values, dtype=float), elapsed_days)

    if slope <= 0:
        return ExhaustionEstimate(slope_per_day=float(slope), projected_at=None)

    current_value = values[-1]
    days_to_ceiling = (ceiling - current_value) / slope
    if days_to_ceiling <= 0:
        # Already at or past the ceiling.
        return ExhaustionEstimate(slope_per_day=float(slope), projected_at=now)
    if days_to_ceiling > EXHAUSTION_MAX_HORIZON_DAYS:
        return ExhaustionEstimate(slope_per_day=float(slope), projected_at=None)

    return ExhaustionEstimate(
        slope_per_day=float(slope), projected_at=now + timedelta(days=days_to_ceiling)
    )
