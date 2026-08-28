"""Train one multivariate novelty model per device from real history.

    python scripts/train_novelty_model.py --days 7
    python scripts/train_novelty_model.py --report        # train nothing, just score

Reads the 1m rollup — the same tier the fleet charts read, so a model is
trained on exactly the numbers a user has already seen — fits an
IsolationForest per device (app/analysis/multivariate.py), and writes each to
`$NOVELTY_MODEL_DIR/{device_id}.joblib`.

Run by hand, not by a worker. That is the deliberate first step: retraining
on a schedule is easy to add on top (the shape is the four existing workers'),
but a model whose training you cannot watch is a bad thing to introduce to a
system whose entire premise is that every number came from somewhere real.
The report this prints — how many rows survived, what the score distribution
looks like, how the newest real reading scores — is the point of running it
manually.

Why a script under backend/scripts/ rather than a module under app/: it
enumerates every device across every tenant, which is exactly what
tests/test_unscoped_import_guard.py exists to keep out of app/. An operator
running a training job on their own database is not a request handler and has
no tenant to scope to.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import joblib
import sqlalchemy as sa

# Importable when run as `python scripts/train_novelty_model.py` from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis.multivariate import (  # noqa: E402
    FEATURES,
    MIN_TRAINING_SAMPLES,
    TrainedModel,
    score_row,
    train,
)
from app.config import get_settings  # noqa: E402
from app.db import AdminSessionLocal  # noqa: E402

DEFAULT_DAYS = 7

# FEATURES is interpolated into a SELECT list, which cannot take a bind
# parameter. It is a module constant rather than anything user-supplied, but
# "it's a constant" is an argument that stops being true the first time
# somebody makes it configurable — so the safety is checked here instead of
# asserted, the same way config.py's ROLE_PASSWORD_RE makes its own
# unavoidable interpolation provably safe.
if not all(name.isidentifier() for name in FEATURES):
    raise RuntimeError(f"FEATURES must all be plain identifiers, got {FEATURES!r}")


def model_path(directory: Path, device_id: uuid.UUID) -> Path:
    return directory / f"{device_id}.joblib"


def _prepare_dir(raw: str) -> Path:
    """Resolve and create the model directory.

    Sync, and called before the async body starts, so the blocking
    filesystem calls are not sitting inside a coroutine — the async in this
    script exists only to reuse the app's DB session.
    """
    directory = Path(raw).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


async def _devices(session) -> list[tuple[uuid.UUID, str, str]]:
    """Every live device. Removed ones are skipped: a novelty model describes
    what a machine's ordinary behaviour looks like, and a machine the user
    deleted has no ongoing behaviour to describe — the same reasoning that
    made /forecasts filter soft-deleted devices."""
    rows = await session.execute(
        sa.text("""
            SELECT id, name, platform FROM devices
            WHERE deleted_at IS NULL ORDER BY name
        """)
    )
    return [(r[0], r[1], r[2]) for r in rows]


async def _history(session, device_id: uuid.UUID, since: datetime) -> list[dict]:
    columns = ", ".join(FEATURES)  # plain identifiers — checked at import
    query = f"""
        SELECT ts, {columns} FROM cagg_metric_samples_1m
        WHERE device_id = :device_id AND ts >= :since
        ORDER BY ts
    """  # noqa: S608 — the only interpolation is FEATURES, validated at import
    rows = await session.execute(
        sa.text(query), {"device_id": device_id, "since": since}
    )
    return [dict(r._mapping) for r in rows]


def _describe(model: TrainedModel, history: list[dict]) -> str:
    """A one-line sanity check on the fit, printed so a bad model is visible
    at training time rather than as strange scores days later."""
    newest = history[-1]
    newest_score = score_row(model, newest)
    scored = [s for row in history if (s := score_row(model, row)) is not None]
    flagged = sum(1 for s in scored if s >= 99.0)
    newest_text = "unavailable" if newest_score is None else f"{newest_score:.1f}"
    return (
        f"newest reading scores {newest_text}, "
        f"{flagged}/{len(scored)} of its own history at >=99"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument(
        "--report",
        action="store_true",
        help="score against the model already on disk; train nothing, write nothing",
    )
    parser.add_argument("--dir", help="override NOVELTY_MODEL_DIR")
    args = parser.parse_args()

    settings = get_settings()
    raw_dir = args.dir or settings.novelty_model_dir
    if not raw_dir:
        print(
            "NOVELTY_MODEL_DIR is not set and --dir was not given.\n"
            "Nothing has been trained, and nothing will be: the API reports no\n"
            "novelty score in that state rather than inventing one.",
            file=sys.stderr,
        )
        return 2
    directory = _prepare_dir(raw_dir)

    since = datetime.now(UTC) - timedelta(days=args.days)
    print(f"{'device':32} {'platform':9} {'rows':>6}  result")
    print("-" * 78)

    trained = skipped = 0
    async with AdminSessionLocal() as session:
        for device_id, name, platform in await _devices(session):
            history = await _history(session, device_id, since)
            path = model_path(directory, device_id)

            if args.report:
                if not path.exists():
                    print(f"{name[:31]:32} {platform:9} {len(history):>6}  no model on disk")
                    continue
                model: TrainedModel = joblib.load(path)
                detail = _describe(model, history) if history else "no history in window"
                print(f"{name[:31]:32} {platform:9} {len(history):>6}  {detail}")
                continue

            model = train(history)
            if model is None:
                skipped += 1
                print(
                    f"{name[:31]:32} {platform:9} {len(history):>6}  "
                    f"skipped — under {MIN_TRAINING_SAMPLES} usable rows"
                )
                continue

            joblib.dump(model, path)
            trained += 1
            print(
                f"{name[:31]:32} {platform:9} {len(history):>6}  "
                f"trained on {model.sample_count}; {_describe(model, history)}"
            )

    if not args.report:
        print("-" * 78)
        print(f"{trained} trained, {skipped} skipped -> {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
