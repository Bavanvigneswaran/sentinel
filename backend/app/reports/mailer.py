"""Emails a rendered report. Same graceful-absence and best-effort posture as
app/alerts/notify.py's `_send_email`: a no-op (logged, not an error) when
`smtp_host` is unset, and a delivery failure is logged and swallowed rather
than raised, so one recipient's bounce or a dead SMTP server never stops
app/workers/report_worker.py's sweep from reaching anyone else's schedule.

Kept separate from alerts/notify.py rather than extended in place: an alert
notification is a short plain-text message to one user's own address, while
a report email carries a binary attachment to a caller-supplied recipient
list (a report schedule's recipients are not necessarily the account's own
email) — different enough in shape that sharing one function would mean
threading an unused parameter through the alert path.
"""

from __future__ import annotations

import logging
from email.message import EmailMessage

import aiosmtplib

from app.config import get_settings

logger = logging.getLogger(__name__)


async def send_report_email(
    *,
    to_addresses: list[str],
    subject: str,
    body: str,
    attachment_bytes: bytes,
    attachment_filename: str,
    attachment_content_type: str,
) -> None:
    settings = get_settings()
    if not settings.smtp_host:
        logger.info("report email suppressed (no smtp_host configured): %s", subject)
        return
    if not to_addresses:
        logger.info("report email suppressed (no recipients): %s", subject)
        return

    message = EmailMessage()
    message["From"] = settings.smtp_from or settings.smtp_username or "sentinel@localhost"
    message["To"] = ", ".join(to_addresses)
    message["Subject"] = subject
    message.set_content(body)

    maintype, _, subtype = attachment_content_type.partition("/")
    message.add_attachment(
        attachment_bytes, maintype=maintype, subtype=subtype, filename=attachment_filename
    )

    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            start_tls=settings.smtp_use_tls,
        )
    except Exception:
        logger.warning(
            "report email dispatch failed to=%s subject=%r", to_addresses, subject, exc_info=True
        )
