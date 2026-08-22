"""Wire schemas for device management and agent enrollment."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The ingest socket's `finally` block sets a device offline on graceful
#: disconnect, but a killed backend process never runs it — the stored
#: `status` column can say "online" forever after a crash. Three missed
#: normal-mode pushes (30s) plus the ingest path's own 15s last-seen-touch
#: throttle, rounded up.
DEVICE_STALE_AFTER_SECONDS = 45


def derive_device_status(status: str, last_seen_at: datetime | None) -> str:
    """Downgrade a stored "online" to "offline" if last_seen_at is too old to
    trust. Never touches "pending" (never connected) or an already-"offline"
    row — this only catches the crash case where the stored value was never
    updated to reflect reality.

    Shared by DeviceOut (below) and app/live/viewer_ws.py, which needs the
    exact same rule to decide whether a freshly-subscribed device is "online"
    and to detect a status change worth pushing to an open viewer socket.
    """
    if status != "online":
        return status
    if last_seen_at is None or (
        datetime.now(UTC) - last_seen_at
    ).total_seconds() > DEVICE_STALE_AFTER_SECONDS:
        return "offline"
    return status


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    hostname: str | None
    os: str | None
    os_version: str | None
    kernel_version: str | None
    arch: str | None
    cpu_cores: int | None
    total_memory_bytes: int | None
    agent_version: str | None
    platform: str
    status: str
    last_seen_at: datetime | None
    enrolled_at: datetime | None
    created_at: datetime

    @model_validator(mode="after")
    def _derive_stale_status(self) -> DeviceOut:
        self.status = derive_device_status(self.status, self.last_seen_at)
        return self


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    platform: Literal["desktop", "android"] = "desktop"


class EnrollmentCodeCreate(BaseModel):
    """Optionally bind the code to an existing device, e.g. to re-enroll after
    reinstalling an agent. Omit it and enrollment creates a new device."""

    device_id: uuid.UUID | None = None
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


class EnrollmentCodeOut(BaseModel):
    id: uuid.UUID
    #: Shown exactly once. Only its sha256 is stored.
    code: str
    expires_at: datetime


class AgentTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID
    token_prefix: str
    name: str | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


# --- the agent-facing enrollment exchange -----------------------------------


class EnrollRequest(BaseModel):
    """Unauthenticated: the one-time code is the credential."""

    code: str = Field(min_length=1, max_length=64)
    device_name: str = Field(min_length=1, max_length=100)
    platform: Literal["desktop", "android"] = "desktop"


class EnrollResponse(BaseModel):
    device_id: uuid.UUID
    #: Returned exactly once. The agent writes it to its config file.
    agent_token: str
