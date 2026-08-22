"""Notification channel settings and web push subscription management."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import CurrentUser, TenantSession
from app.config import get_settings
from app.models.notifications import NotificationSettings, WebPushSubscription
from app.schemas.notifications import (
    NotificationSettingsOut,
    NotificationSettingsUpdate,
    VapidPublicKeyOut,
    WebPushSubscribeRequest,
    WebPushUnsubscribeRequest,
)

router = APIRouter(tags=["notifications"])


async def _get_or_create_settings(session, user_id) -> NotificationSettings:  # noqa: ANN001
    settings = await session.get(NotificationSettings, user_id)
    if settings is None:
        settings = NotificationSettings(user_id=user_id)
        session.add(settings)
        await session.commit()
    return settings


@router.get("/notifications/settings", response_model=NotificationSettingsOut)
async def get_notification_settings(
    user: CurrentUser, session: TenantSession
) -> NotificationSettings:
    return await _get_or_create_settings(session, user.id)


@router.patch("/notifications/settings", response_model=NotificationSettingsOut)
async def update_notification_settings(
    payload: NotificationSettingsUpdate, user: CurrentUser, session: TenantSession
) -> NotificationSettings:
    settings = await _get_or_create_settings(session, user.id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(settings, field, value)
    await session.commit()
    # updated_at's onupdate is server-evaluated — see the identical comment on
    # update_alert_rule in api/routes/alerts.py.
    await session.refresh(settings)
    return settings


@router.get("/notifications/vapid-public-key", response_model=VapidPublicKeyOut)
async def get_vapid_public_key(user: CurrentUser) -> VapidPublicKeyOut:
    return VapidPublicKeyOut(public_key=get_settings().vapid_public_key)


@router.post("/notifications/web-push/subscribe", status_code=204)
async def subscribe_web_push(
    payload: WebPushSubscribeRequest, user: CurrentUser, session: TenantSession
) -> None:
    """Upsert on endpoint: the same browser subscribing under a different
    account (or re-subscribing after the row already exists) reassigns
    ownership rather than raising a spurious conflict."""
    stmt = pg_insert(WebPushSubscription).values(
        user_id=user.id,
        endpoint=payload.endpoint,
        p256dh=payload.p256dh,
        auth=payload.auth,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["endpoint"],
        set_={"user_id": user.id, "p256dh": payload.p256dh, "auth": payload.auth},
    )
    await session.execute(stmt)
    await session.commit()


@router.delete("/notifications/web-push/subscribe", status_code=204)
async def unsubscribe_web_push(
    payload: WebPushUnsubscribeRequest, user: CurrentUser, session: TenantSession
) -> None:
    # RLS already prevents deleting another tenant's row even if an endpoint
    # string were guessed; the user_id filter here is belt-and-braces.
    await session.execute(
        sa.delete(WebPushSubscription).where(
            WebPushSubscription.user_id == user.id,
            WebPushSubscription.endpoint == payload.endpoint,
        )
    )
    await session.commit()
