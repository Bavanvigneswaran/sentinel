"""Wire schemas for incidents: the fleet-wide list, per-incident detail with
its correlated-event timeline, and the generated summary/root-cause
fields, which are plain strings — display text only, never structured
output the frontend interprets as anything but prose. See
app/insights/generator.py, which writes them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.alerts import AlertEventOut

IncidentStatus = Literal["open", "resolved"]


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID
    status: IncidentStatus
    opened_at: datetime
    closed_at: datetime | None
    summary_text: str | None
    summary_model: str | None
    summary_generated_at: datetime | None
    root_cause_text: str | None
    root_cause_model: str | None
    root_cause_generated_at: datetime | None


class IncidentDetailOut(IncidentOut):
    events: list[AlertEventOut]
