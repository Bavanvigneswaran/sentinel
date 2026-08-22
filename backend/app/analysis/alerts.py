"""The alert state machine: OK -> PENDING -> FIRING -> OK, with a `for:`
duration gate before a PENDING condition is allowed to fire.

No I/O, no DB — same style as analysis/health.py. Given the current stored
state and one fresh evaluation, `step()` says what the new state is and
whether this tick is the one that should open or close an alert_event. That
"only true on the tick it actually happens" property is the whole dedup
story: the caller creates a new event exactly when `fire` is True and closes
the open one exactly when `resolve` is True, never by re-deriving it from
`state` alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

State = Literal["ok", "pending", "firing"]
Comparison = Literal[">", ">=", "<", "<=", "=="]

_OPS = {
    ">": lambda value, threshold: value > threshold,
    ">=": lambda value, threshold: value >= threshold,
    "<": lambda value, threshold: value < threshold,
    "<=": lambda value, threshold: value <= threshold,
    "==": lambda value, threshold: value == threshold,
}


def evaluate_condition(value: float, comparison: Comparison, threshold: float) -> bool:
    return _OPS[comparison](value, threshold)


@dataclass(frozen=True)
class StepResult:
    state: State
    pending_since: datetime | None
    #: True exactly on the tick that should open a new alert_event.
    fire: bool
    #: True exactly on the tick that should close the currently open event.
    resolve: bool


def step(
    *,
    state: State,
    pending_since: datetime | None,
    #: None means no fresh sample this tick — a missing reading is never
    #: treated as "condition cleared" or "condition met", it simply leaves
    #: the prior state exactly as it was.
    condition_met: bool | None,
    now: datetime,
    for_duration_seconds: int,
) -> StepResult:
    if condition_met is None:
        return StepResult(state=state, pending_since=pending_since, fire=False, resolve=False)

    if condition_met:
        if state == "ok":
            return StepResult(state="pending", pending_since=now, fire=False, resolve=False)
        if state == "pending":
            assert pending_since is not None  # noqa: S101 — invariant: PENDING always carries it
            elapsed = (now - pending_since).total_seconds()
            if elapsed >= for_duration_seconds:
                return StepResult(
                    state="firing", pending_since=pending_since, fire=True, resolve=False
                )
            return StepResult(
                state="pending", pending_since=pending_since, fire=False, resolve=False
            )
        # state == "firing": condition still holds, nothing changes. This is
        # the dedup guarantee — fire is False on every tick after the first.
        return StepResult(state="firing", pending_since=pending_since, fire=False, resolve=False)

    # condition_met is False.
    if state == "firing":
        return StepResult(state="ok", pending_since=None, fire=False, resolve=True)
    if state == "pending":
        # Cleared before for_duration elapsed: back to OK with no event ever
        # having existed to resolve.
        return StepResult(state="ok", pending_since=None, fire=False, resolve=False)
    return StepResult(state="ok", pending_since=None, fire=False, resolve=False)
