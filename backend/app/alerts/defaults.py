"""The rules every account gets without asking for them.

Until now nothing in this system detected anything on its own: the evaluator,
the anomaly baselines and the forecast-breach check all existed and all did
nothing until a user hand-wrote a rule. A brand-new account with three
machines enrolled would sit there, healthy-looking and silent, through a disk
filling to 100% — not because detection failed but because nothing had been
asked to look.

These are the "asked to look" part, created once per account. They are
ordinary `AlertRule` rows, deliberately not a parallel mechanism: the
evaluator sweep, the OK/PENDING/FIRING state machine, incident correlation and
notification dispatch all treat them exactly like a hand-written rule, because
they *are* hand-written rules that the account did not have to write. Adding a
second "automatic" evaluation path beside the rule evaluator is the thing this
codebase has refused to do three times already (Phase 6's anomaly rules, Phase
7's forecast rules, Phase 8's incident correlation all went through the
existing choke points instead).

`device_id` is None on every one of them, which is load-bearing: the evaluator
fans a null-device rule out across the caller's devices at sweep time
(`_rule_device_pairs`), so these cover machines enrolled *after* the account
was created without anything having to re-seed them. That is why this is
per-account and not per-device.

Two things the user keeps control of:

* They are editable and deletable like any other rule. `source` marks where a
  rule came from so the UI can group them and explain them, not to make them
  read-only — a default that cannot be turned off is a worse default.
* Seeding happens exactly once (see `ensure_default_rules`). A rule the user
  deleted stays deleted; a threshold they tuned stays tuned.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AlertRule

#: Marks a rule this module created, as opposed to one a user wrote.
SOURCE_BUILTIN = "builtin"
SOURCE_USER = "user"
RULE_SOURCES = (SOURCE_USER, SOURCE_BUILTIN)


@dataclass(frozen=True)
class DefaultRule:
    name: str
    rule_type: str
    metric: str
    comparison: str | None = None
    threshold: float | None = None
    for_duration_seconds: int = 300
    #: Rendered in the UI under the rule, so somebody who did not write it can
    #: tell what it is for without reverse-engineering the numbers.
    description: str = ""


#: Deliberately quiet. A default rule set that pages on a laptop compiling
#: something is a default rule set people switch off within a day, and the
#: cost of missing a slow-moving problem by ten minutes is far lower than the
#: cost of teaching somebody to ignore the alerts. Hence high thresholds and
#: long durations on the static rules, with the adaptive ones carrying the
#: burden of catching what a fixed number cannot.
DEFAULT_RULES: tuple[DefaultRule, ...] = (
    # --- static thresholds: the unambiguous "this is bad on any machine" set.
    DefaultRule(
        name="CPU pinned",
        rule_type="threshold",
        metric="cpu_percent",
        comparison=">",
        threshold=90,
        for_duration_seconds=600,
        description="CPU has been above 90% for ten minutes straight.",
    ),
    DefaultRule(
        name="Memory nearly full",
        rule_type="threshold",
        metric="mem_percent",
        comparison=">",
        threshold=90,
        for_duration_seconds=600,
        description="Memory has been above 90% for ten minutes straight.",
    ),
    DefaultRule(
        name="Disk nearly full",
        rule_type="threshold",
        metric="disk_percent",
        comparison=">",
        threshold=90,
        for_duration_seconds=600,
        description="The fullest mount on this machine is over 90% used.",
    ),
    DefaultRule(
        name="Network dropping packets",
        rule_type="threshold",
        metric="packet_loss_percent",
        comparison=">",
        threshold=20,
        for_duration_seconds=300,
        description="More than a fifth of packets to a latency target are being lost.",
    ),
    # --- adaptive: what a fixed threshold cannot express. A machine that
    # normally sits at 8% CPU running at 45% is not "high" by any absolute
    # number, and is exactly the thing worth knowing about.
    DefaultRule(
        name="Unusual CPU for this machine",
        rule_type="anomaly",
        metric="cpu_percent",
        for_duration_seconds=300,
        description=(
            "CPU is far outside what is normal for this particular machine, "
            "learned from its own history rather than a fixed threshold."
        ),
    ),
    DefaultRule(
        name="Unusual memory for this machine",
        rule_type="anomaly",
        metric="mem_percent",
        for_duration_seconds=300,
        description=(
            "Memory is far outside this machine's own learned normal — catches a "
            "leak long before it reaches any absolute threshold."
        ),
    ),
    # --- predictive: the breach that has not happened yet.
    DefaultRule(
        name="Disk predicted to fill",
        rule_type="forecast",
        metric="disk_percent",
        comparison=">",
        threshold=90,
        for_duration_seconds=0,
        description=(
            "This machine's disk is trending towards over 90% within the "
            "forecast horizon. Fires on the prediction, not on the reading."
        ),
    ),
    DefaultRule(
        name="Memory predicted to fill",
        rule_type="forecast",
        metric="mem_percent",
        comparison=">",
        threshold=90,
        for_duration_seconds=0,
        description="Memory is trending towards over 90% within the forecast horizon.",
    ),
)


def _to_row(rule: DefaultRule, user_id: uuid.UUID) -> AlertRule:
    return AlertRule(
        user_id=user_id,
        device_id=None,  # every device the account has, now and later
        name=rule.name,
        rule_type=rule.rule_type,
        metric=rule.metric,
        comparison=rule.comparison,
        threshold=rule.threshold,
        for_duration_seconds=rule.for_duration_seconds,
        enabled=True,
        source=SOURCE_BUILTIN,
    )


async def ensure_default_rules(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Seed the default rule set for one account. Returns how many were added.

    Idempotent, and idempotent in the way that matters: it asks whether this
    account has *ever* had a builtin rule, not whether it currently has all
    eight. Checking for each rule individually would resurrect every default
    the user deliberately deleted, every time this ran — a "default" that
    reappears after you delete it is not a default, it is a bug the user
    cannot work around.
    """
    already_seeded = await session.scalar(
        sa.select(sa.literal(1))
        .select_from(AlertRule)
        .where(AlertRule.user_id == user_id, AlertRule.source == SOURCE_BUILTIN)
        .limit(1)
    )
    if already_seeded:
        return 0

    for rule in DEFAULT_RULES:
        session.add(_to_row(rule, user_id))
    await session.flush()
    return len(DEFAULT_RULES)
