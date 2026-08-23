"""ReportWorker.run_once() against a real due ReportSchedule — same style as
test_forecast_worker.py and test_alert_notifications.py, since the
background loop itself never runs in the test suite (see the module's own
docstring). The mailer's actual SMTP send is monkeypatched, same posture
test_alert_notifications.py takes with notify_firing/notify_resolved — this
module's concern is "does the worker decide correctly and update
last_sent_at", not aiosmtplib's own wire behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.db import AdminSessionLocal
from app.ingest.writer import write_samples
from app.models import ReportSchedule, User
from app.schemas.protocol import Sample, SystemSample
from app.services import enrollment_service as svc
from app.workers import report_worker as worker_module
from app.workers.report_worker import ReportWorker

# A Monday, so a weekly schedule with day_of_week=0 is due.
NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)


@pytest.fixture
async def user_and_device(admin_session):
    user = User(email="report-worker-owner@example.com", password_hash="x")
    admin_session.add(user)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="report-worker-box")

    async with AdminSessionLocal() as session:
        await write_samples(
            session,
            device_id=device.id,
            user_id=user.id,
            samples=[
                Sample(
                    ts=NOW - timedelta(hours=1),
                    resolution_seconds=10,
                    system=SystemSample(cpu_percent=42.0),
                )
            ],
            now=NOW - timedelta(hours=1),
        )
    return {"user": user, "device": device}


@pytest.fixture
def sent_emails(monkeypatch):
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(worker_module, "send_report_email", fake_send)
    return calls


async def _schedule_for(schedule_id) -> ReportSchedule | None:
    async with AdminSessionLocal() as session:
        return await session.get(ReportSchedule, schedule_id)


async def test_a_due_weekly_schedule_is_sent_and_marked(user_and_device, sent_emails):
    user = user_and_device["user"]
    async with AdminSessionLocal() as session:
        schedule = ReportSchedule(
            user_id=user.id,
            name="Weekly summary",
            cadence="weekly",
            day_of_week=0,
            format="pdf",
        )
        session.add(schedule)
        await session.commit()
        await session.refresh(schedule)

    await ReportWorker().run_once(now=NOW)

    assert len(sent_emails) == 1
    assert sent_emails[0]["to_addresses"] == [user.email]
    assert sent_emails[0]["attachment_filename"] == "sentinel-report.pdf"
    assert sent_emails[0]["attachment_bytes"][:5] == b"%PDF-"

    refreshed = await _schedule_for(schedule.id)
    assert refreshed.last_sent_at == NOW


async def test_a_schedule_not_due_today_is_not_sent(user_and_device, sent_emails):
    user = user_and_device["user"]
    async with AdminSessionLocal() as session:
        # NOW is a Monday (weekday 0); day_of_week=2 is Wednesday.
        session.add(
            ReportSchedule(
                user_id=user.id, name="Wed summary", cadence="weekly", day_of_week=2, format="pdf"
            )
        )
        await session.commit()

    await ReportWorker().run_once(now=NOW)

    assert sent_emails == []


async def test_a_disabled_schedule_is_never_swept(user_and_device, sent_emails):
    user = user_and_device["user"]
    async with AdminSessionLocal() as session:
        session.add(
            ReportSchedule(
                user_id=user.id,
                name="Disabled",
                cadence="weekly",
                day_of_week=0,
                format="pdf",
                enabled=False,
            )
        )
        await session.commit()

    await ReportWorker().run_once(now=NOW)

    assert sent_emails == []


async def test_a_schedule_already_sent_today_is_not_sent_again(user_and_device, sent_emails):
    user = user_and_device["user"]
    async with AdminSessionLocal() as session:
        session.add(
            ReportSchedule(
                user_id=user.id,
                name="Weekly summary",
                cadence="weekly",
                day_of_week=0,
                format="pdf",
                last_sent_at=NOW - timedelta(hours=2),
            )
        )
        await session.commit()

    await ReportWorker().run_once(now=NOW)

    assert sent_emails == []


async def test_csv_format_sends_a_real_csv_attachment(user_and_device, sent_emails):
    user = user_and_device["user"]
    async with AdminSessionLocal() as session:
        session.add(
            ReportSchedule(
                user_id=user.id,
                name="CSV export",
                cadence="weekly",
                day_of_week=0,
                format="csv",
            )
        )
        await session.commit()

    await ReportWorker().run_once(now=NOW)

    assert len(sent_emails) == 1
    assert sent_emails[0]["attachment_filename"] == "sentinel-report.csv"
    assert sent_emails[0]["attachment_bytes"].decode("utf-8").startswith("device,")


async def test_explicit_recipients_override_the_account_email(user_and_device, sent_emails):
    user = user_and_device["user"]
    async with AdminSessionLocal() as session:
        session.add(
            ReportSchedule(
                user_id=user.id,
                name="Weekly summary",
                cadence="weekly",
                day_of_week=0,
                format="pdf",
                recipients=["ops-team@example.com"],
            )
        )
        await session.commit()

    await ReportWorker().run_once(now=NOW)

    assert sent_emails[0]["to_addresses"] == ["ops-team@example.com"]


async def test_a_device_scoped_schedule_only_reports_that_device(user_and_device, sent_emails):
    user, device = user_and_device["user"], user_and_device["device"]
    async with AdminSessionLocal() as session:
        await svc.register_device(session, user_id=user.id, name="other-box")
        session.add(
            ReportSchedule(
                user_id=user.id,
                device_id=device.id,
                name="Just this box",
                cadence="weekly",
                day_of_week=0,
                format="csv",
            )
        )
        await session.commit()

    await ReportWorker().run_once(now=NOW)

    csv_text = sent_emails[0]["attachment_bytes"].decode("utf-8")
    assert device.name in csv_text
    assert "other-box" not in csv_text


async def test_multiple_users_are_each_swept_independently(admin_session, sent_emails):
    async with AdminSessionLocal() as session:
        user_a = User(email="worker-multi-a@example.com", password_hash="x")
        user_b = User(email="worker-multi-b@example.com", password_hash="x")
        session.add_all([user_a, user_b])
        await session.commit()
        session.add_all(
            [
                ReportSchedule(
                    user_id=user_a.id, name="a", cadence="weekly", day_of_week=0, format="pdf"
                ),
                ReportSchedule(
                    user_id=user_b.id, name="b", cadence="weekly", day_of_week=0, format="pdf"
                ),
            ]
        )
        await session.commit()

    await ReportWorker().run_once(now=NOW)

    assert {call["to_addresses"][0] for call in sent_emails} == {
        "worker-multi-a@example.com",
        "worker-multi-b@example.com",
    }
