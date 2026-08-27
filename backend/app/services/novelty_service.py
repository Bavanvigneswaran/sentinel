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

    _CACHE[key] = model
    return model


async def score_device(
    session: AsyncSession, device_id: uuid.UUID, *, now: datetime | None = None
) -> NoveltyResult | NoveltyUnavailable:
    """Score this device's newest reading. The session must already be
    tenant-scoped — same requirement as fleet_service.build_summaries()."""
    now = now or datetime.now(UTC)

    if model_dir() is None:
        return NoveltyUnavailable(reason="No novelty models have been trained on this server yet.")

    model = load_model(device_id)
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
