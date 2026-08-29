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

from app.alerts import fcm
from app.config import get_settings
from app.models.alerts import AlertEvent
from app.models.notifications import FcmToken, NotificationSettings, WebPushSubscription
from app.models.user import User
from app.security.outbound import resolve_is_public

logger = logging.getLogger(__name__)


def _subject(event: AlertEvent, device_name: str, *, resolved: bool) -> str:
    verb = "resolved" if resolved else "firing"
    return f"[Sentinel] {event.rule_name} {verb} on {device_name}"


def _condition(event) -> str:
    """What this event's rule was actually judging, in words.

    `rule_type` is the discriminator, never `comparison` — an anomaly event
    leaves comparison/threshold null and carries baseline/z-score evidence
    instead, and a forecast event sets the same two fields as a threshold
    event while meaning something quite different by them (see
    app/models/alerts.py). Reading them unconditionally is what put a literal
    "(None None)" into every anomaly notification: both frontends already
    branch on rule_type, and this is the one surface that did not.
    """
    if event.rule_type == "anomaly":
        if event.baseline_mean is None or event.z_score is None:
            return "unusual for this device"
        return (
            f"unusual for this device — normally around "
            f"{event.baseline_mean:.1f}, now {event.z_score:+.1f} sigma out"
        )
    if event.rule_type == "multivariate":
        # A percentile against this machine's own history, not a reading, so
        # the bare "> 71.0" below would be actively misleading.
        return f"an unusual combination of readings, scoring above {event.threshold:.0f}/100"
    clause = f"{event.comparison} {event.threshold}"
    if event.rule_type == "forecast":
        when = (
            f" by {event.predicted_breach_at:%Y-%m-%d %H:%M UTC}"
            if event.predicted_breach_at is not None
            else ""
        )
        predicted = (
            f" (predicted {event.predicted_value:.1f})"
            if event.predicted_value is not None
            else ""
        )
        return f"forecast to reach {clause}{when}{predicted}"
    return clause


def _body(event: AlertEvent, device_name: str, *, resolved: bool) -> str:
    condition = _condition(event)
    if event.rule_type == "multivariate":
        # `metric` is the reserved "novelty_score", which is a schema detail
        # rather than something to show a person — every other rule type has a
        # metric name that means something on its own, and this one does not.
        if resolved:
            return (
                f"{event.rule_name} on {device_name} has resolved.\n"
                f"The overall pattern of readings is back to normal "
                f"({event.resolved_value:.0f}/100 for unusualness)."
            )
        # `condition` is deliberately unused on this branch: it restates the
        # threshold, which the sentence below already carries, and stacking
        # the two said "unusual" three times.
        threshold = f"{event.threshold:.0f}" if event.threshold is not None else "the threshold"
        return (
            f"{event.rule_name} is firing on {device_name}.\n"
            f"Its readings together score {event.value_at_fire:.0f}/100 for how unusual "
            f"they are for this machine, past the {threshold} you set."
        )
    if resolved:
        return (
            f"{event.rule_name} on {device_name} has resolved.\n"
            f"{event.metric} is now {event.resolved_value} "
            f"(was {condition})."
        )
    return (
        f"{event.rule_name} is firing on {device_name}.\n"
        f"{event.metric} = {event.value_at_fire} ({condition})."
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
        # Checked again here, not just at registration. The schema validates the
        # endpoint's shape offline; only an actual resolution can say where the
        # name points *now*, and a row written before that validation existed
        # has never been checked at all. A skip, not a delete: a transient DNS
        # failure must not discard a legitimate subscription.
        if not await asyncio.to_thread(resolve_is_public, sub.endpoint):
            logger.warning(
                "web push skipped, endpoint does not resolve to a public address endpoint=%s",
                sub.endpoint,
            )
            continue
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


async def _send_fcm(session: AsyncSession, user_id: uuid.UUID, payload: dict) -> None:
    """Push to every Android device this user has registered.

    Unlike web push there is no `fcm_enabled` setting to consult: holding a
    token row IS the opt-in, because a phone that revoked the OS permission or
    uninstalled the app cannot be represented honestly by an account-wide flag.
    See the FcmToken model docstring.
    """
    if not fcm.is_configured():
        logger.info("fcm suppressed (not configured)")
        return

    tokens = list(await session.scalars(sa.select(FcmToken).where(FcmToken.user_id == user_id)))
    if not tokens:
        return

    dead = await fcm.send_to_tokens(
        [t.token for t in tokens],
        title=payload["title"],
        body=payload["body"],
        # Every FCM data value must be a string. `url` is the same "/alerts"
        # the web push payload already carries, so one notification body serves
        # both channels; the app maps it through an allow-list rather than
        # navigating to whatever arrives (frontend/mobile/src/navigation/linking.ts).
        data={"url": payload["url"]},
    )
    if dead:
        await session.execute(sa.delete(FcmToken).where(FcmToken.token.in_(dead)))


async def _dispatch(
    session: AsyncSession,
    user_id: uuid.UUID,
    event: AlertEvent,
    device_name: str,
    *,
    resolved: bool,
) -> None:
    push_payload = {
        "title": _subject(event, device_name, resolved=resolved),
        "body": _body(event, device_name, resolved=resolved),
        "url": "/alerts",
    }

    # Deliberately before the settings lookup, and not gated on it. The row is
    # created lazily by GET /notifications/settings, which the Android app
    # never calls — it has no email or web-push UI to render. Gating FCM on a
    # row that only the web console creates would mean a phone that registered
    # a token silently received nothing until its owner happened to open
    # Settings in a browser.
    await _send_fcm(session, user_id, push_payload)

    notification_settings = await session.get(NotificationSettings, user_id)
    if notification_settings is None:
        return

    if notification_settings.email_enabled:
        user = await session.get(User, user_id)
        to_address = notification_settings.email_address or (user.email if user else None)
        if to_address:
            await _send_email(to_address, push_payload["title"], push_payload["body"])

    if notification_settings.web_push_enabled:
        await _send_web_push(session, user_id, push_payload)


async def notify_firing(
    session: AsyncSession, user_id: uuid.UUID, event: AlertEvent, device_name: str
) -> None:
    await _dispatch(session, user_id, event, device_name, resolved=False)


async def notify_resolved(
    session: AsyncSession, user_id: uuid.UUID, event: AlertEvent, device_name: str
) -> None:
    await _dispatch(session, user_id, event, device_name, resolved=True)
