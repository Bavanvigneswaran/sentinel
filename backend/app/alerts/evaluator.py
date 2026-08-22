"""The alert evaluator: a global periodic sweep, not a per-connection loop.

Shape mirrors app/live/supervisor.py's `_reconcile_loop` (`while True: work();
sleep(interval)`), but there is no per-connection anchor for this one — it is
started once from app/main.py's lifespan and runs for the life of the process.

Tenancy: the evaluator has no request and no JWT, so there is no natural
TenantSession. Exactly ONE query per tick runs unscoped — `SELECT DISTINCT
user_id FROM alert_rules WHERE enabled` — which is why app.alerts.evaluator
has an entry in tests/test_unscoped_import_guard.py's ALLOWED dict. Every
other read and write in the sweep happens inside a session scoped to that
one user via `scope_to_user()`, exactly like a request's own session.

`run_once(now=...)` is public and takes an injectable `now` on purpose:
tests/conftest.py's `client` fixture never runs FastAPI's lifespan
(httpx's ASGITransport doesn't invoke it, and the `live_server` fixture
passes `lifespan="off"`), so this loop never actually runs in the test
suite — evaluator tests call `run_once()` directly across synthetic ticks.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import notify
from app.analysis.alerts import evaluate_condition, step
from app.config import get_settings
from app.db import AdminSessionLocal, SessionLocal, scope_to_user
from app.models import AlertEvent, AlertRule, AlertSilence, AlertState, Device
from app.services.metrics_read import FRESH_WINDOW_SECONDS, latest_per_entity

logger = logging.getLogger(__name__)

#: Metrics read straight off metric_samples, one row per device — no worst-of
#: multiple entities needed.
_SYSTEM_METRICS = frozenset({"cpu_percent", "mem_percent", "swap_percent", "cpu_iowait_percent"})


async def _read_latest_values(
    session: AsyncSession,
    metrics: set[str],
    device_ids: list[uuid.UUID],
    now: datetime,
) -> dict[tuple[str, uuid.UUID], float | None]:
    """Current value of every requested metric, per device.

    At most three queries regardless of how many rules or devices the user
    has — one per source table an enabled rule actually references, never one
    query per device. A missing entry (rather than an explicit None) and an
    explicit None both mean "no fresh reading"; callers read this with
    `.get()` so the two are indistinguishable, which is the point — a gap in
    the data is never treated as a value.
    """
    since = now - timedelta(seconds=FRESH_WINDOW_SECONDS)
    values: dict[tuple[str, uuid.UUID], float | None] = {}

    system_metrics = tuple(metrics & _SYSTEM_METRICS)
    if system_metrics:
        rows = await latest_per_entity(
            session,
            table="metric_samples",
            entity_keys=(),
            columns=system_metrics,
            since=since,
            device_ids=device_ids,
        )
        for row in rows:
            for metric in system_metrics:
                values[(metric, row["device_id"])] = row[metric]

    if "disk_percent" in metrics:
        rows = await latest_per_entity(
            session,
            table="disk_usage_samples",
            entity_keys=("mount",),
            columns=("percent",),
            since=since,
            device_ids=device_ids,
        )
        # The fullest mount, matching analysis/health.py's own choice of which
        # disk figure represents "the device's" disk capacity.
        worst: dict[uuid.UUID, float] = {}
        for row in rows:
            if row["percent"] is None:
                continue
            worst[row["device_id"]] = max(worst.get(row["device_id"], 0.0), row["percent"])
        for device_id in device_ids:
            values[("disk_percent", device_id)] = worst.get(device_id)

    if "packet_loss_percent" in metrics:
        rows = await latest_per_entity(
            session,
            table="latency_samples",
            entity_keys=("target",),
            columns=("packet_loss_percent",),
            since=since,
            device_ids=device_ids,
        )
        # The worst target, not the mean — one dead link is what an alert
        # should catch, and averaging it against healthy ones would hide it.
        worst = {}
        for row in rows:
            if row["packet_loss_percent"] is None:
                continue
            worst[row["device_id"]] = max(
                worst.get(row["device_id"], 0.0), row["packet_loss_percent"]
            )
        for device_id in device_ids:
            values[("packet_loss_percent", device_id)] = worst.get(device_id)

    return values


class AlertEvaluator:
    def __init__(self) -> None:
        self._interval_seconds = get_settings().alert_evaluator_interval_seconds

    async def run(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("alert evaluator tick failed")
            await asyncio.sleep(self._interval_seconds)

    async def run_once(self, now: datetime | None = None) -> None:
        """One full sweep across every tenant with an enabled rule."""
        now = now or datetime.now(UTC)
        for user_id in await self._enabled_rule_user_ids():
            try:
                await self._evaluate_user(user_id, now)
            except Exception:
                # One tenant's bad data or a transient failure must not stop
                # the sweep from reaching everyone else.
                logger.exception("alert evaluation failed user_id=%s", user_id)

    async def _enabled_rule_user_ids(self) -> list[uuid.UUID]:
        """The one unscoped query in this module — see the module docstring."""
        async with AdminSessionLocal() as session:
            rows = await session.scalars(
                sa.select(AlertRule.user_id).where(AlertRule.enabled.is_(True)).distinct()
            )
            return list(rows)

    async def _evaluate_user(self, user_id: uuid.UUID, now: datetime) -> None:
        async with SessionLocal() as session:
            scope_to_user(session, user_id)

            rules = list(
                await session.scalars(sa.select(AlertRule).where(AlertRule.enabled.is_(True)))
            )
            if not rules:
                return

            devices_by_id = {
                d.id: d
                for d in await session.scalars(
                    sa.select(Device).where(Device.deleted_at.is_(None))
                )
            }
            pairs = self._rule_device_pairs(rules, list(devices_by_id))
            if not pairs:
                return

            values = await _read_latest_values(
                session, {rule.metric for rule, _ in pairs}, list(devices_by_id), now
            )
            existing_states = {
                (s.rule_id, s.device_id): s
                for s in await session.scalars(
                    sa.select(AlertState).where(AlertState.rule_id.in_([r.id for r in rules]))
                )
            }

            for rule, device_id in pairs:
                await self._evaluate_pair(
                    session,
                    user_id,
                    rule,
                    devices_by_id[device_id],
                    existing_states.get((rule.id, device_id)),
                    values.get((rule.metric, device_id)),
                    now,
                )

            await session.commit()

    @staticmethod
    def _rule_device_pairs(
        rules: list[AlertRule], device_ids: list[uuid.UUID]
    ) -> list[tuple[AlertRule, uuid.UUID]]:
        device_id_set = set(device_ids)
        pairs: list[tuple[AlertRule, uuid.UUID]] = []
        for rule in rules:
            if rule.device_id is not None:
                # A device-scoped rule whose device was deleted has nothing
                # left to evaluate — skip silently rather than erroring.
                if rule.device_id in device_id_set:
                    pairs.append((rule, rule.device_id))
            else:
                pairs.extend((rule, device_id) for device_id in device_ids)
        return pairs

    async def _evaluate_pair(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        rule: AlertRule,
        device: Device,
        state_row: AlertState | None,
        value: float | None,
        now: datetime,
    ) -> None:
        if state_row is None:
            state_row = AlertState(
                user_id=user_id, rule_id=rule.id, device_id=device.id, state="ok"
            )
            session.add(state_row)

        condition_met = (
            None if value is None else evaluate_condition(value, rule.comparison, rule.threshold)
        )
        result = step(
            state=state_row.state,  # type: ignore[arg-type]
            pending_since=state_row.pending_since,
            condition_met=condition_met,
            now=now,
            for_duration_seconds=rule.for_duration_seconds,
        )

        state_row.state = result.state
        state_row.pending_since = result.pending_since
        state_row.last_evaluated_at = now
        if value is not None:
            state_row.last_value = value

        if result.fire:
            assert value is not None  # noqa: S101 — step() only fires when condition_met is True
            event = AlertEvent(
                user_id=user_id,
                rule_id=rule.id,
                device_id=device.id,
                rule_name=rule.name,
                metric=rule.metric,
                comparison=rule.comparison,
                threshold=rule.threshold,
                status="firing",
                value_at_fire=value,
                last_value=value,
                fired_at=now,
            )
            session.add(event)
            await session.flush()  # assigns event.id
            state_row.current_event_id = event.id
            await self._maybe_notify(session, user_id, rule, device, event, now, resolved=False)

        elif result.resolve and state_row.current_event_id is not None:
            event = await session.get(AlertEvent, state_row.current_event_id)
            if event is not None:
                event.status = "resolved"
                event.resolved_at = now
                event.resolved_value = value
                if value is not None:
                    event.last_value = value
                await self._maybe_notify(session, user_id, rule, device, event, now, resolved=True)
            state_row.current_event_id = None

        elif (
            state_row.state == "firing"
            and state_row.current_event_id is not None
            and value is not None
        ):
            # Still firing and re-evaluated: keep the open event's last-seen
            # value current without touching notified_at again.
            event = await session.get(AlertEvent, state_row.current_event_id)
            if event is not None:
                event.last_value = value

    async def _maybe_notify(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        rule: AlertRule,
        device: Device,
        event: AlertEvent,
        now: datetime,
        *,
        resolved: bool,
    ) -> None:
        if await self._is_silenced(session, user_id, rule.id, device.id, now):
            # Leave notified_at None: "fired but never paged" stays distinct
            # from "paged, and every channel happened to fail".
            return
        try:
            if resolved:
                await notify.notify_resolved(session, user_id, event, device.name)
            else:
                await notify.notify_firing(session, user_id, event, device.name)
        except Exception:
            logger.exception(
                "notification dispatch failed rule_id=%s device_id=%s resolved=%s",
                rule.id,
                device.id,
                resolved,
            )
        event.notified_at = now

    async def _is_silenced(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        rule_id: uuid.UUID,
        device_id: uuid.UUID,
        now: datetime,
    ) -> bool:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(AlertSilence)
            .where(
                AlertSilence.user_id == user_id,
                sa.or_(AlertSilence.device_id.is_(None), AlertSilence.device_id == device_id),
                sa.or_(AlertSilence.rule_id.is_(None), AlertSilence.rule_id == rule_id),
                AlertSilence.starts_at <= now,
                AlertSilence.ends_at >= now,
            )
        )
        return bool(count)
