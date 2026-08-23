"""Analytics/report API: the JSON trend+availability+reliability view, an
on-demand PDF/CSV download, and scheduled-report CRUD.

Simple CRUD lives directly here, matching alerts.py's and forecasts.py's
style; the one piece of real logic (assembling the bundle, rendering it) is
in app/services/report_service.py and app/reports/, shared with
app/workers/report_worker.py's scheduled emails so an on-demand download and
a mailed report can never disagree about what a device's numbers were.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, TenantSession
from app.models import Device
from app.models.reports import ReportSchedule
from app.reports.csv_export import render_report_csv
from app.reports.mailer import send_report_email
from app.reports.pdf import render_report_pdf
from app.schemas.reports import (
    AnalyticsReportOut,
    AvailabilityOut,
    DeviceAnalyticsOut,
    MetricTrendOut,
    PeriodStatsOut,
    ReliabilityOut,
    ReportScheduleCreate,
    ReportScheduleOut,
    ReportScheduleUpdate,
)
from app.services.report_service import DEFAULT_PERIOD_DAYS, ReportBundle, build_report

router = APIRouter(tags=["reports"])

_CONTENT_TYPES = {"pdf": "application/pdf", "csv": "text/csv"}


def _bundle_to_out(bundle: ReportBundle) -> AnalyticsReportOut:
    return AnalyticsReportOut(
        generated_at=bundle.generated_at,
        period_start=bundle.period_start,
        period_end=bundle.period_end,
        period_days=bundle.period_days,
        devices=[
            DeviceAnalyticsOut(
                device_id=da.device.id,
                device_name=da.device.name,
                availability=AvailabilityOut(
                    uptime_percent=da.availability.uptime_percent,
                    reporting_seconds=da.availability.reporting_seconds,
                    period_seconds=da.availability.period_seconds,
                ),
                reliability=ReliabilityOut(
                    incident_count=da.reliability.incident_count,
                    resolved_incident_count=da.reliability.resolved_incident_count,
                    mean_time_to_resolve_seconds=da.reliability.mean_time_to_resolve_seconds,
                    alert_fired_count=da.reliability.alert_fired_count,
                ),
                trends=[
                    MetricTrendOut(
                        metric=t.metric,
                        entity=t.entity,
                        current=PeriodStatsOut(
                            avg=t.current.avg, min=t.current.min, max=t.current.max
                        ),
                        previous=PeriodStatsOut(
                            avg=t.previous.avg, min=t.previous.min, max=t.previous.max
                        ),
                        delta_percent=t.delta_percent,
                    )
                    for t in da.trends
                ],
            )
            for da in bundle.devices
        ],
    )


async def _existing_device_or_404(session, device_id: uuid.UUID | None) -> None:
    if device_id is None:
        return
    exists = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Device)
        .where(Device.id == device_id, Device.deleted_at.is_(None))
    )
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")


def _device_ids_param(device_id: uuid.UUID | None) -> list[uuid.UUID] | None:
    return [device_id] if device_id is not None else None


# --- analytics -----------------------------------------------------------------


@router.get("/reports/analytics", response_model=AnalyticsReportOut)
async def get_analytics(
    user: CurrentUser,
    session: TenantSession,
    device_id: uuid.UUID | None = None,
    period_days: int = Query(default=DEFAULT_PERIOD_DAYS, ge=1, le=366),
) -> AnalyticsReportOut:
    await _existing_device_or_404(session, device_id)
    bundle = await build_report(
        session, device_ids=_device_ids_param(device_id), period_days=period_days
    )
    return _bundle_to_out(bundle)


@router.get("/reports/export.csv")
async def export_csv(
    user: CurrentUser,
    session: TenantSession,
    device_id: uuid.UUID | None = None,
    period_days: int = Query(default=DEFAULT_PERIOD_DAYS, ge=1, le=366),
) -> Response:
    await _existing_device_or_404(session, device_id)
    bundle = await build_report(
        session, device_ids=_device_ids_param(device_id), period_days=period_days
    )
    csv_text = render_report_csv(bundle)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="sentinel-report.csv"'},
    )


@router.get("/reports/export.pdf")
async def export_pdf(
    user: CurrentUser,
    session: TenantSession,
    device_id: uuid.UUID | None = None,
    period_days: int = Query(default=DEFAULT_PERIOD_DAYS, ge=1, le=366),
) -> Response:
    await _existing_device_or_404(session, device_id)
    bundle = await build_report(
        session, device_ids=_device_ids_param(device_id), period_days=period_days
    )
    pdf_bytes = await asyncio.to_thread(render_report_pdf, bundle)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="sentinel-report.pdf"'},
    )


# --- scheduled reports -----------------------------------------------------


@router.get("/reports/schedules", response_model=list[ReportScheduleOut])
async def list_report_schedules(user: CurrentUser, session: TenantSession) -> list[ReportSchedule]:
    rows = await session.scalars(
        sa.select(ReportSchedule).order_by(ReportSchedule.created_at)
    )
    return list(rows)


@router.post(
    "/reports/schedules", response_model=ReportScheduleOut, status_code=status.HTTP_201_CREATED
)
async def create_report_schedule(
    payload: ReportScheduleCreate, user: CurrentUser, session: TenantSession
) -> ReportSchedule:
    await _existing_device_or_404(session, payload.device_id)
    schedule = ReportSchedule(user_id=user.id, **payload.model_dump())
    session.add(schedule)
    await session.commit()
    return schedule


@router.patch("/reports/schedules/{schedule_id}", response_model=ReportScheduleOut)
async def update_report_schedule(
    schedule_id: uuid.UUID,
    payload: ReportScheduleUpdate,
    user: CurrentUser,
    session: TenantSession,
) -> ReportSchedule:
    schedule = await session.get(ReportSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report schedule not found"
        )

    updates = payload.model_dump(exclude_unset=True)
    if "device_id" in updates:
        await _existing_device_or_404(session, updates["device_id"])
    for field, value in updates.items():
        setattr(schedule, field, value)

    try:
        await session.commit()
    except sa.exc.IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="day_of_week must be set (and day_of_month unset) for a weekly schedule, and "
            "vice versa for monthly",
        ) from exc
    # updated_at's onupdate is server-evaluated — see the identical comment on
    # update_alert_rule in api/routes/alerts.py.
    await session.refresh(schedule)
    return schedule


@router.delete("/reports/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report_schedule(
    schedule_id: uuid.UUID, user: CurrentUser, session: TenantSession
) -> None:
    result = await session.execute(
        sa.delete(ReportSchedule).where(ReportSchedule.id == schedule_id)
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report schedule not found"
        )
    await session.commit()


@router.post("/reports/schedules/{schedule_id}/send-now", status_code=status.HTTP_204_NO_CONTENT)
async def send_report_schedule_now(
    schedule_id: uuid.UUID, user: CurrentUser, session: TenantSession
) -> None:
    """Bypasses is_due()'s own cadence check: a user pressing "send now"
    wants a report today even if it isn't a scheduled day — the same
    deliberate crack in an automatic cadence that incidents.py's
    "regenerate" route opens for the insights cache, open only to an
    explicit user action, never to the background sweep."""
    schedule = await session.get(ReportSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report schedule not found"
        )

    device_ids = [schedule.device_id] if schedule.device_id is not None else None
    now = datetime.now(UTC)
    bundle = await build_report(
        session, device_ids=device_ids, period_days=schedule.period_days, now=now
    )

    if schedule.format == "pdf":
        attachment = await asyncio.to_thread(render_report_pdf, bundle)
        filename = "sentinel-report.pdf"
    else:
        attachment = render_report_csv(bundle).encode("utf-8")
        filename = "sentinel-report.csv"

    recipients = schedule.recipients or [user.email]
    await send_report_email(
        to_addresses=recipients,
        subject=f"[Sentinel] {schedule.name}",
        body=(
            f"Your Sentinel report \"{schedule.name}\" for "
            f"{bundle.period_start.date()} – {bundle.period_end.date()} is attached."
        ),
        attachment_bytes=attachment,
        attachment_filename=filename,
        attachment_content_type=_CONTENT_TYPES[schedule.format],
    )
    schedule.last_sent_at = now
    await session.commit()
