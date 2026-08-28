"""Wire schema for the layer-4 multivariate novelty score.

One shape covers both outcomes rather than two response models, because the
client renders them in the same slot and a 404 for "not trained yet" would be
wrong — the device exists, the score does not. `available` discriminates, and
`reason` is filled exactly when it is false, so the UI can state which of the
several "no score" situations this is instead of guessing.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DeviceNoveltyOut(BaseModel):
    available: bool
    #: 0-100, a percentile against this device's own training history.
    #: 0 is "as ordinary as this machine gets", 100 is "stranger than
    #: anything in the training window". Null whenever `available` is false.
    score: float | None = None
    #: Why there is no score. Null when there is one.
    reason: str | None = None
    trained_on_samples: int | None = None
    #: Which metrics the model actually judges — worth showing, because the
    #: set is smaller than the protocol's and the omissions are deliberate
    #: (see app/analysis/multivariate.py's FEATURES).
    feature_names: list[str] | None = None
    reading_ts: datetime | None = None
