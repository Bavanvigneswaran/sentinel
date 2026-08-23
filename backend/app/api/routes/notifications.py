"""Notification channel settings and web push subscription management."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, status
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DatabaseError

from app.api.deps import CurrentUser, TenantSession
from app.config import get_settings
from app.models.notifications import FcmToken, NotificationSettings, WebPushSubscription
from app.schemas.notifications import (
    FcmRegisterRequest,
    FcmUnregisterRequest,
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


# --- FCM (Android) -----------------------------------------------------------
# Deliberately the same shape as the web-push pair above: upsert on the opaque
# device identifier, delete by the same. The Phase 10a viewer is the only
# caller.


@router.post("/notifications/fcm/register", status_code=204)
async def register_fcm_token(
    payload: FcmRegisterRequest, user: CurrentUser, session: TenantSession
) -> None:
    """Upsert on token: FCM hands the same device the same token across app
    launches, and the app re-registers on every Settings visit, so this must be
    idempotent rather than accumulating rows.

    Registering IS enabling. There is no settings flag to flip alongside it;
    see the FcmToken model docstring.

    The one case the upsert cannot resolve is a token already owned by a
    *different* tenant — one phone signed out of account A and into account B.
    RLS makes that row invisible, so the ON CONFLICT DO UPDATE cannot see the
    row it would have to rewrite and Postgres refuses the statement outright.
    That is the correct outcome, not a limitation to work around: silently
    reassigning a device across accounts from a request that cannot even see
    the existing row would be exactly the kind of cross-tenant write the
    policies exist to stop. It surfaces as an actionable 409 instead of a 500,
    and the Android app avoids reaching it at all by unregistering on sign-out
    (see frontend/mobile/src/stores/auth.ts).
    """
    stmt = pg_insert(FcmToken).values(
        user_id=user.id,
        token=payload.token,
        device_label=payload.device_label,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["token"],
        set_={"user_id": user.id, "device_label": payload.device_label},
    )
    try:
        await session.execute(stmt)
        await session.commit()
    except DatabaseError as exc:
        # The transaction is aborted at this point; roll back before the
        # dependency tries to close a session in a failed state.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This device is registered to a different Sentinel account. "
                "Turn push off there, or sign out of the app on that account, first."
            ),
        ) from exc


@router.delete("/notifications/fcm/register", status_code=204)
async def unregister_fcm_token(
    payload: FcmUnregisterRequest, user: CurrentUser, session: TenantSession
) -> None:
    # RLS already prevents deleting another tenant's row even if a token string
    # were guessed; the user_id filter here is belt-and-braces.
    await session.execute(
        sa.delete(FcmToken).where(FcmToken.user_id == user.id, FcmToken.token == payload.token)
    )
    await session.commit()
