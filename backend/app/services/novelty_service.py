"""Reads a trained layer-4 model off disk and scores a device's current
reading with it.

The training half is backend/scripts/train_novelty_model.py, run by hand; the
maths is app/analysis/multivariate.py, pure. This module is only the join
between them: find the model, find the newest real reading, return a score or
an honest reason there isn't one.

Every "no score" case is distinct and named (`NoveltyUnavailable.reason`)
rather than collapsed into a null. "Nothing has been trained yet", "this
device has no model", and "the device has not reported recently" are three
different situations with three different fixes, and a single null would make
the UI guess between them — the same argument lib/deviceNames.ts makes for
keeping "removed" and "not loaded" apart.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import joblib
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.multivariate import TrainedModel, score_row
from app.config import get_settings
from app.services.metrics_read import FRESH_WINDOW_SECONDS, latest_per_entity

logger = logging.getLogger(__name__)

#: Loaded models, keyed by (path, mtime_ns). Unpickling a forest costs real
#: milliseconds and a model changes only when the training script rewrites it,
#: so keying on mtime means a retrain is picked up on the next request with no
#: restart — the same "re-read rather than cache indefinitely" posture
#: download_service takes with the build manifest.
#:
#: At most one entry per path: a retrain writes a new mtime, and the previous
#: entry for that device is dropped when the new one lands (see load_model).
#: Keying on mtime *without* that eviction is a leak rather than a cache — a
#: fitted forest is ~26 MB unpickled here, so a nightly retrain would add that
#: per device per night to a `make serve` process that never restarts, and the
#: superseded models are unreachable by then anyway.
_CACHE: dict[tuple[str, int], TrainedModel] = {}


@dataclass(frozen=True)
class NoveltyResult:
    score: float
    trained_on_samples: int
    feature_names: tuple[str, ...]
    reading_ts: datetime


@dataclass(frozen=True)
class NoveltyUnavailable:
    reason: str


def model_dir() -> Path | None:
    raw = get_settings().novelty_model_dir
    return Path(raw).expanduser().resolve() if raw else None


def load_model(device_id: uuid.UUID) -> TrainedModel | None:
    """The model for one device, or None when there isn't a usable one.

    `joblib.load` is pickle, and unpickling executes arbitrary code. That is
    acceptable here for one specific reason: these files are written by an
    operator running the training script against their own database, into a
    directory that operator named — they are never uploaded, never
    user-supplied, and never fetched over a network. If that ever stops being
    true, this needs a serialisation format that is not pickle, not a
    sandbox.

    The isinstance check below is therefore not a security boundary; it is a
    guard against a stale or truncated file from an interrupted training run
    turning into an AttributeError somewhere further down.
    """
    directory = model_dir()
    if directory is None:
        return None
    path = directory / f"{device_id}.joblib"
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None

    key = (str(path), mtime)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    try:
        model = joblib.load(path)
    except Exception:
        logger.exception("could not load novelty model path=%s", path)
        return None
    if not isinstance(model, TrainedModel):
        logger.error("novelty model at %s is not a TrainedModel: %r", path, type(model))
        return None

    # Drop any older mtime for this same path before inserting: the file has
    # been rewritten, so every previous entry for it describes a model that
    # can never be asked for again.
    #
    # `pop(..., None)` rather than `del` because this now runs in a worker
    # thread (see _load_models): two callers racing on the same freshly
    # retrained model would both load it and both try to evict the same stale
    # key. That race is benign — the two loads are equal and one simply wins —
    # but a bare `del` would turn it into a KeyError.
    for stale in [k for k in _CACHE if k[0] == key[0]]:
        _CACHE.pop(stale, None)
    _CACHE[key] = model
    return model


def _load_models(device_ids: list[uuid.UUID]) -> dict[uuid.UUID, TrainedModel]:
    """Every model that exists, for one thread hop rather than N.

    Sync on purpose: the caller runs it through asyncio.to_thread, and doing
    the whole batch inside one hop keeps the cost independent of how many
    devices a sweep covers — the same reasoning score_devices() applies to its
    single query.
    """
    return {d: m for d in device_ids if (m := load_model(d)) is not None}


def _score_rows(
    models: dict[uuid.UUID, TrainedModel], rows: list[dict[str, Any]]
) -> dict[uuid.UUID, float]:
    """Score every row against its own device's model.

    Called directly on the event loop, **not** through asyncio.to_thread, and
    that is the opposite of what Phase 7 did for the ETS fit. Measured rather
    than assumed, because the obvious move is actively worse here: scoring 50
    devices inline stalls the loop 91.7ms, and the same work through
    to_thread stalls it 109.6ms. sklearn's score_samples() holds the GIL, so a
    worker thread does not free the loop — it just adds a hop and GIL
    contention on top of the same block. to_thread only pays off for work that
    releases the GIL, which is why load_model() *is* offloaded (joblib.load
    does file I/O: 9.9ms inline vs 5.1ms offloaded).

    The cost is per *model*, not per row: score_samples() on one row costs
    1.76ms and on fifty rows 1.75ms, i.e. it is almost entirely fixed per-call
    overhead. So a sweep pays ~1.8ms per device that has both a model and a
    multivariate rule. Two devices here; at fifty this would want a real
    answer — a process pool, or scoring on the training script's schedule
    rather than every sweep — and not another to_thread.
    """
    scores: dict[uuid.UUID, float] = {}
    for row in rows:
        device_id = row["device_id"]
        model = models.get(device_id)
        if model is None:
            continue
        score = score_row(model, row)
        if score is not None:
            scores[device_id] = score
    return scores


async def score_device(
    session: AsyncSession, device_id: uuid.UUID, *, now: datetime | None = None
) -> NoveltyResult | NoveltyUnavailable:
    """Score this device's newest reading. The session must already be
    tenant-scoped — same requirement as fleet_service.build_summaries()."""
    now = now or datetime.now(UTC)

    if model_dir() is None:
        return NoveltyUnavailable(reason="No novelty models have been trained on this server yet.")

    model = await asyncio.to_thread(load_model, device_id)
    if model is None:
        return NoveltyUnavailable(
            reason="No model for this device yet — it needs more reporting history."
        )

    rows: list[dict[str, Any]] = await latest_per_entity(
        session,
        table="metric_samples",
        entity_keys=(),
        columns=model.feature_names,
        since=now - timedelta(seconds=FRESH_WINDOW_SECONDS),
        device_ids=[device_id],
    )
    if not rows:
        # Same rule as every headline number on a summary: a reading older
        # than the freshness window is not "current", and scoring it would
        # describe a moment that has passed.
        return NoveltyUnavailable(reason="No reading in the last 90 seconds to score.")

    reading = rows[0]
    # Scored inline, deliberately — see _score_rows() for the measurement.
    score = score_row(model, reading)
    if score is None:
        missing = [f for f in model.feature_names if reading.get(f) is None]
        return NoveltyUnavailable(
            reason=(
                "This device does not report every metric the model needs: " + ", ".join(missing)
            )
        )

    return NoveltyResult(
        score=score,
        trained_on_samples=model.sample_count,
        feature_names=model.feature_names,
        reading_ts=reading["ts"],
    )


async def score_devices(
    session: AsyncSession,
    device_ids: list[uuid.UUID],
    *,
    now: datetime | None = None,
) -> dict[uuid.UUID, float]:
    """Novelty for many devices in one query, for the evaluator sweep.

    score_device() is the right shape for a request about one device and the
    wrong shape for a sweep: it reads the newest reading per call, so N
    devices would be N queries. This reads them all once, matching the
    "at most three queries regardless of how many rules or devices" budget
    evaluator._read_latest_values() already keeps.

    Devices with no model, no fresh reading, or a reading that cannot fill
    the vector are simply absent from the result. The caller reads it with
    .get(), so absent and unscorable are indistinguishable — the same
    deliberate conflation _read_latest_values() makes, because both mean
    "no answer", and the state machine treats a missing value as unknown
    rather than as normal.
    """
    now = now or datetime.now(UTC)
    # One thread hop for every model the sweep needs, not one per device:
    # joblib.load() releases the GIL for its file I/O, so this genuinely keeps
    # a cold load off the loop (the sweep runs on the same loop that carries
    # live-mode agent sockets). Scoring is not offloaded — see _score_rows().
    models = await asyncio.to_thread(_load_models, device_ids)
    if not models:
        return {}

    # Union across models rather than assuming they share FEATURES: a device
    # trained before the feature set last changed still has its own list, and
    # asking for a column its model does not use is harmless.
    columns = tuple({name for model in models.values() for name in model.feature_names})
    rows = await latest_per_entity(
        session,
        table="metric_samples",
        entity_keys=(),
        columns=columns,
        since=now - timedelta(seconds=FRESH_WINDOW_SECONDS),
        device_ids=list(models),
    )

    return _score_rows(models, rows)
