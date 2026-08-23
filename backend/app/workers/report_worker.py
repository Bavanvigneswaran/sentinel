"""The report worker: a periodic sweep that emails every due ReportSchedule.

Shape mirrors app/workers/forecast_worker.py and app/workers/insights_worker.py
almost exactly: a global periodic task, one unscoped enumeration query, every
subsequent read or write through a session scoped via scope_to_user() — see
either module's docstring for the tenancy reasoning, which applies unchanged
here. `app.analysis.report_schedule.is_due()` is the only logic deciding
*whether* to send; this module's job is purely "for each due schedule, build
the bundle, render it, and mail it."
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from app.analysis.report_schedule import is_due
from app.config import get_settings
from app.db import AdminSessionLocal, SessionLocal, scope_to_user
from app.models import ReportSchedule, User
from app.reports.csv_export import render_report_csv
from app.reports.mailer import send_report_email
from app.reports.pdf import render_report_pdf
from app.services.report_service import build_report

logger = logging.getLogger(__name__)

_CONTENT_TYPES = {"pdf": "application/pdf", "csv": "text/csv"}


class ReportWorker:
    def __init__(self) -> None:
        settings = get_settings()
        self._interval_seconds = settings.report_worker_interval_seconds

    async def run(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("report worker tick failed")
            await asyncio.sleep(self._interval_seconds)

    async def run_once(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        for user_id in await self._users_with_enabled_schedules():
            try:
                await self._send_due_for_user(user_id, now)
            except Exception:
                # One tenant's failure (a bad SMTP config, a rendering error)
                # must not stop the sweep for everyone else.
                logger.exception("report sweep failed user_id=%s", user_id)

    async def _users_with_enabled_schedules(self) -> list[uuid.UUID]:
        """The one unscoped query in this module — see the module docstring."""
        async with AdminSessionLocal() as session:
            rows = await session.scalars(
                sa.select(ReportSchedule.user_id).where(ReportSchedule.enabled).distinct()
            )
            return list(rows)

    async def _send_due_for_user(self, user_id: uuid.UUID, now: datetime) -> None:
        async with SessionLocal() as session:
            scope_to_user(session, user_id)
            user = await session.get(User, user_id)
            if user is None:
                return

            schedules = list(
                await session.scalars(
                    sa.select(ReportSchedule).where(ReportSchedule.enabled)
                )
            )
            for schedule in schedules:
                if not is_due(
                    cadence=schedule.cadence,
                    day_of_week=schedule.day_of_week,
                    day_of_month=schedule.day_of_month,
                    last_sent_at=schedule.last_sent_at,
                    now=now,
                ):
                    continue
                try:
                    await self._send_one(session, schedule, user, now)
                except Exception:
                    logger.exception("failed to send report schedule_id=%s", schedule.id)
                    continue
                schedule.last_sent_at = now
            await session.commit()

    async def _send_one(
        self, session, schedule: ReportSchedule, user: User, now: datetime
    ) -> None:
        device_ids = [schedule.device_id] if schedule.device_id is not None else None
        bundle = await build_report(
            session, device_ids=device_ids, period_days=schedule.period_days, now=now
        )

        if schedule.format == "pdf":
            attachment = await asyncio.to_thread(render_report_pdf, bundle)
            filename = "sentinel-report.pdf"
        else:
            attachment = (await asyncio.to_thread(render_report_csv, bundle)).encode("utf-8")
            filename = "sentinel-report.csv"

        recipients = schedule.recipients or [user.email]
        await send_report_email(
            to_addresses=recipients,
            subject=f"[Sentinel] {schedule.name}",
            body=(
                f"Your scheduled Sentinel report \"{schedule.name}\" for "
                f"{bundle.period_start.date()} – {bundle.period_end.date()} is attached."
            ),
            attachment_bytes=attachment,
            attachment_filename=filename,
            attachment_content_type=_CONTENT_TYPES[schedule.format],
        )
