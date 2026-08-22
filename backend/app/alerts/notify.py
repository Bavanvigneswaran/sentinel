"""Dispatches a firing or resolved alert to whichever channels the user has
enabled. Every send is independently best-effort: a dead SMTP server or one
expired push endpoint must never stop evaluation for other users or other
channels — same philosophy as app/live/bus.py's `publish_samples`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from email.message import EmailMessage

import aiosmtplib
import sqlalchemy as sa
from pywebpush import WebPushException, webpush
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.alerts import AlertEvent
from app.models.notifications import NotificationSettings, WebPushSubscription
from app.models.user import User

logger = logging.getLogger(__name__)


def _subject(event: AlertEvent, device_name: str, *, resolved: bool) -> str:
    verb = "resolved" if resolved else "firing"
    return f"[Sentinel] {event.rule_name} {verb} on {device_name}"


def _body(event: AlertEvent, device_name: str, *, resolved: bool) -> str:
    if resolved:
        return (
            f"{event.rule_name} on {device_name} has resolved.\n"
            f"{event.metric} is now {event.resolved_value} "
            f"(threshold was {event.comparison} {event.threshold})."
        )
    return (
        f"{event.rule_name} is firing on {device_name}.\n"
        f"{event.metric} = {event.value_at_fire} "
        f"({event.comparison} {event.threshold})."
    )


async def _send_email(to_address: str, subject: str, body: str) -> None:
    settings = get_settings()
    if not settings.smtp_host:
        logger.info("email suppressed (no smtp_host configured): %s", subject)
        return
    message = EmailMessage()
    message["From"] = settings.smtp_from or settings.smtp_username or "sentinel@localhost"
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)
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
        logger.warning("email dispatch failed to=%s subject=%r", to_address, subject, exc_info=True)


async def _send_web_push(session: AsyncSession, user_id: uuid.UUID, payload: dict) -> None:
    settings = get_settings()
    if not (settings.vapid_public_key and settings.vapid_private_key and settings.vapid_subject):
        logger.info("web push suppressed (VAPID not configured)")
        return

    subscriptions = list(
        await session.scalars(
            sa.select(WebPushSubscription).where(WebPushSubscription.user_id == user_id)
        )
    )
    for sub in subscriptions:
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=json.dumps(payload),
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
            )
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (404, 410):
                # The browser has dropped this subscription; routine cleanup.
                await session.execute(
                    sa.delete(WebPushSubscription).where(WebPushSubscription.id == sub.id)
                )
            else:
                logger.warning(
                    "web push failed endpoint=%s status=%s", sub.endpoint, status, exc_info=True
                )
        except Exception:
            logger.warning("web push failed endpoint=%s", sub.endpoint, exc_info=True)


async def _dispatch(
    session: AsyncSession,
    user_id: uuid.UUID,
    event: AlertEvent,
    device_name: str,
    *,
    resolved: bool,
) -> None:
    notification_settings = await session.get(NotificationSettings, user_id)
    if notification_settings is None:
        return

    if notification_settings.email_enabled:
        user = await session.get(User, user_id)
        to_address = notification_settings.email_address or (user.email if user else None)
        if to_address:
            await _send_email(
                to_address,
                _subject(event, device_name, resolved=resolved),
                _body(event, device_name, resolved=resolved),
            )

    if notification_settings.web_push_enabled:
        await _send_web_push(
            session,
            user_id,
            {
                "title": _subject(event, device_name, resolved=resolved),
                "body": _body(event, device_name, resolved=resolved),
                "url": "/alerts",
            },
        )


async def notify_firing(
    session: AsyncSession, user_id: uuid.UUID, event: AlertEvent, device_name: str
) -> None:
    await _dispatch(session, user_id, event, device_name, resolved=False)


async def notify_resolved(
    session: AsyncSession, user_id: uuid.UUID, event: AlertEvent, device_name: str
) -> None:
    await _dispatch(session, user_id, event, device_name, resolved=True)
