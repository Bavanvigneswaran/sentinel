"""Wire schemas for device management and agent enrollment."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
