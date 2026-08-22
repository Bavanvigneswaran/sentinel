"""SQLAlchemy ORM models.

Every model MUST be imported here. Alembic autogenerate only sees what is present
in Base.metadata, and a forgotten import silently produces a DROP TABLE.

Pydantic wire schemas live in app/schemas/, not here.
"""

from app.models.agent_token import AgentToken
from app.models.alerts import AlertEvent, AlertRule, AlertSilence, AlertState
from app.models.base import Base
from app.models.device import Device
from app.models.enrollment_code import EnrollmentCode
from app.models.metrics import (
    DiskIoSample,
    DiskUsageSample,
    LatencySample,
    MetricSample,
    NetSample,
    ProcessSample,
)
from app.models.notifications import NotificationSettings, WebPushSubscription
from app.models.refresh_token import RefreshToken
from app.models.user import User

__all__ = [
    "AgentToken",
    "AlertEvent",
    "AlertRule",
    "AlertSilence",
    "AlertState",
    "Base",
    "Device",
    "DiskIoSample",
    "DiskUsageSample",
    "EnrollmentCode",
    "LatencySample",
    "MetricSample",
    "NetSample",
    "NotificationSettings",
    "ProcessSample",
    "RefreshToken",
    "User",
    "WebPushSubscription",
]
