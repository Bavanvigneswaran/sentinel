"""Wire schemas for notification channel settings and web push subscriptions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.security.outbound import UnsafeUrl, validate_push_endpoint

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
    """A browser's push subscription.

    `endpoint` is the one URL in this product that a user chooses and the server
    later fetches (app/alerts/notify.py sends to it on every alert), so it is
    validated as a request target rather than merely as a string. Without this
    the field was a length bound and nothing else, and an authenticated user
    could point the server at a metadata service or a loopback port.
    """

    endpoint: str = Field(min_length=1, max_length=2000)
    p256dh: str = Field(min_length=1, max_length=500)
    auth: str = Field(min_length=1, max_length=500)

    @field_validator("endpoint")
    @classmethod
    def _endpoint_is_a_safe_target(cls, value: str) -> str:
        try:
            return validate_push_endpoint(value)
        except UnsafeUrl as exc:
            raise ValueError(str(exc)) from exc


class WebPushUnsubscribeRequest(BaseModel):
    """Deliberately *not* validated the way subscribing is.

    A row written before that validation existed must stay removable, and
    deleting by an exact string cannot reach anything a caller does not already
    own — RLS scopes the statement and the route filters on user_id as well.
    """

    endpoint: str = Field(min_length=1, max_length=2000)


class VapidPublicKeyOut(BaseModel):
    #: None when the server has no VAPID keypair configured.
    public_key: str | None


class FcmRegisterRequest(BaseModel):
    """Register (or re-register) this Android device for alert notifications.

    There is no matching `fcm_enabled` field on NotificationSettingsUpdate:
    holding a row here *is* the opt-in — see the FcmToken model docstring.
    """

    #: FCM registration tokens are ~160 chars today but are documented as
    #: variable-length and opaque; the cap is generous and exists only to keep
    #: an unbounded string out of the database.
    token: str = Field(min_length=1, max_length=4096)
    device_label: str | None = Field(default=None, max_length=200)


class FcmUnregisterRequest(BaseModel):
    token: str = Field(min_length=1, max_length=4096)
