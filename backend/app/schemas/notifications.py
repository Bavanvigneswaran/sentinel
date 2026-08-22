"""Wire schemas for notification channel settings and web push subscriptions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Sensitivity = Literal["low", "medium", "high"]


class NotificationSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email_enabled: bool
    email_address: str | None
    web_push_enabled: bool
    #: Stored now; read by nothing until Phase 6's adaptive baseline.
    anomaly_sensitivity: Sensitivity
    updated_at: datetime


class NotificationSettingsUpdate(BaseModel):
    """All fields optional; PATCH applies only what's present. An explicit
    `email_address: null` clears the override back to the account email."""

    email_enabled: bool | None = None
    email_address: EmailStr | None = None
    web_push_enabled: bool | None = None
    anomaly_sensitivity: Sensitivity | None = None


class WebPushSubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)
    p256dh: str = Field(min_length=1, max_length=500)
    auth: str = Field(min_length=1, max_length=500)


class WebPushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)


class VapidPublicKeyOut(BaseModel):
    #: None when the server has no VAPID keypair configured.
    public_key: str | None
