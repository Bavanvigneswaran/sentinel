"""Per-user notification preferences and web push subscriptions."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, in_check, uuid_pk

SENSITIVITIES = ("low", "medium", "high")


class NotificationSettings(Base):
    """One row per user, created lazily on first read (see
    app/api/routes/notifications.py). `anomaly_sensitivity` is stored now but
    read by nothing until Phase 6's adaptive baseline.
    """

    __tablename__ = "notification_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    email_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    #: None falls back to the account's own email at send time.
    email_address: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    web_push_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    anomaly_sensitivity: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'medium'")
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    __table_args__ = (
        sa.CheckConstraint(
            in_check("anomaly_sensitivity", SENSITIVITIES), name="anomaly_sensitivity"
        ),
    )


class WebPushSubscription(Base):
    """A browser's Push API subscription. A user can hold several — one per
    browser/device that has enabled push."""

    __tablename__ = "web_push_subscriptions"
    __table_args__ = (sa.Index("ix_web_push_subscriptions_user_id", "user_id"),)

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    endpoint: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(sa.Text, nullable=False)
    auth: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class FcmToken(Base):
    """An Android device's FCM registration token.

    The mobile counterpart of WebPushSubscription, and deliberately shaped the
    same way: one row per device, `token` unique so a re-register upserts
    rather than duplicating, and a user can hold several (two phones, or a
    phone and a tablet).

    There is no `fcm_enabled` flag on NotificationSettings beside
    `web_push_enabled`. Registration *is* the enable flag: a settings row
    saying "on" while no device token exists would be a lie the UI would
    happily render, and the two would drift the first moment somebody revoked
    the OS permission. A row here means that device opted in; deleting it means
    it opted out.
    """

    __tablename__ = "fcm_tokens"
    __table_args__ = (sa.Index("ix_fcm_tokens_user_id", "user_id"),)

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    #: Whatever the phone calls itself. Display only; never trusted for routing.
    device_label: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
