"""The viewer WebSocket protocol.

A browser subscribes to one or more of its own devices and receives the same
`Sample` objects the agent pushed, fanned out over Redis. Untrusted the same
way agent frames are: size-capped before parsing, schema-validated before any
Redis or database work, and a `device_id` in a `subscribe` frame is only ever
resolved through an RLS-scoped ownership check — the channel it maps to is
already tenant-scoped by construction (see app/live/bus.py), but the
ownership check is the primary defence, not the channel naming.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.protocol import (
    DiskIoEntry,
    DiskUsageEntry,
    LatencyEntry,
    NetEntry,
    ProcessEntry,
    Sample,
    SystemSample,
)

#: A subscribe/unsubscribe/pong frame is tiny; this is generous headroom, not
#: a sizing target.
MAX_VIEWER_FRAME_BYTES = 4 * 1024

#: One browser tab watching every device on a large fleet is a real workflow;
#: this bounds it without dedicating a socket per device.
MAX_SUBSCRIPTIONS_PER_SOCKET = 8


class _Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- frames: browser → server ------------------------------------------------


class SubscribeFrame(_Frame):
    type: Literal["subscribe"] = "subscribe"
    device_id: uuid.UUID


class UnsubscribeFrame(_Frame):
    type: Literal["unsubscribe"] = "unsubscribe"
    device_id: uuid.UUID


class ViewerPongFrame(_Frame):
    type: Literal["pong"] = "pong"


ViewerFrame = Annotated[
    SubscribeFrame | UnsubscribeFrame | ViewerPongFrame,
    Field(discriminator="type"),
]


# --- frames: server → browser ------------------------------------------------


class SubscribedFrame(_Frame):
    type: Literal["subscribed"] = "subscribed"
    device_id: uuid.UUID
    #: Whether the device is currently connected and pushing at all — distinct
    #: from whether *this* subscription has pushed the agent into live mode,
    #: which happens regardless and is not worth a separate signal here.
    online: bool


class SamplesFrame(_Frame):
    type: Literal["samples"] = "samples"
    device_id: uuid.UUID
    samples: list[Sample]


class DeviceStatusFrame(_Frame):
    """Pushed whenever a subscribed device's derived status changes."""

    type: Literal["device_status"] = "device_status"
    device_id: uuid.UUID
    status: Literal["pending", "online", "offline"]


class ViewerPingFrame(_Frame):
    type: Literal["ping"] = "ping"


class ViewerErrorFrame(_Frame):
    type: Literal["error"] = "error"
    code: Literal[
        "unauthorized", "invalid_frame", "too_large", "not_found", "too_many_subscriptions"
    ]
    message: str
    #: The device this error concerns, when it is scoped to one subscribe
    #: attempt rather than the whole socket.
    device_id: uuid.UUID | None = None


ServerViewerFrame = (
    SubscribedFrame | SamplesFrame | DeviceStatusFrame | ViewerPingFrame | ViewerErrorFrame
)


# --- REST: viewer tickets and the recent-samples primer ----------------------


class TicketOut(BaseModel):
    #: Single-use, 30s TTL. Spent immediately as a `?ticket=` query param on
    #: the `/ws/viewer` connect.
    ticket: str


class SystemPoint(SystemSample):
    ts: datetime


class DiskIoPoint(DiskIoEntry):
    ts: datetime


class NetPoint(NetEntry):
    ts: datetime


class LatencyPoint(LatencyEntry):
    ts: datetime


class DiskUsageSnapshot(DiskUsageEntry):
    ts: datetime


class ProcessSnapshot(ProcessEntry):
    ts: datetime


class RecentSamplesOut(BaseModel):
    """Primes a freshly-opened chart while the agent's live-mode upshift is
    still in flight. disk_usage and processes are a single latest snapshot,
    not a series — both change far less often than a per-second gauge, and
    the frontend only ever shows their current value, never a history."""

    device_id: uuid.UUID
    since: datetime
    system: list[SystemPoint]
    disk_io: list[DiskIoPoint]
    net: list[NetPoint]
    latency: list[LatencyPoint]
    disk_usage: list[DiskUsageSnapshot]
    processes: list[ProcessSnapshot]
