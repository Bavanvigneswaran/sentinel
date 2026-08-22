"""Wire schemas for the auth endpoints.

Pydantic models live in app/schemas/; SQLAlchemy ORM classes live in app/models/.
These are the contract that TS types are generated from.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.config import get_settings

_settings = get_settings()


class SignupRequest(BaseModel):
    email: EmailStr
    # The upper bound is not cosmetic: argon2 handles long inputs fine, but an
    # unbounded password field is a free CPU-amplification vector.
    password: str = Field(
        min_length=_settings.password_min_length, max_length=_settings.password_max_length
    )
    display_name: str | None = Field(default=None, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    # Deliberately min_length=1, not the signup minimum: enforcing the policy
    # here would leak it, and would let an attacker skip candidate passwords.
    password: str = Field(min_length=1, max_length=_settings.password_max_length)


class UserOut(BaseModel):
    """Has no password_hash field at all, so it cannot leak by accident.
    Relying on an `exclude` would be one refactor away from a breach."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    display_name: str | None
    created_at: datetime
    last_login_at: datetime | None


class SessionResponse(BaseModel):
    """The access token travels in the body and is held in memory by the client.

    The refresh token is never in a body — it exists only as an HttpOnly cookie.
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 — a scheme name
    expires_in: int  # seconds; the client schedules a proactive refresh off this
    user: UserOut
