from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, sha256_hash, ts_now, uuid_pk

ENROLLMENT_CODE_TTL_SECONDS = 15 * 60


class EnrollmentCode(Base):
    """A short-lived one-time code a user generates to enroll a new agent.

    Consumption must go through a single atomic UPDATE ... WHERE consumed_at IS
    NULL AND expires_at > now() RETURNING — never read-then-write.
    """

    __tablename__ = "enrollment_codes"
    __table_args__ = (
        sa.Index("ix_enrollment_codes_user_id", "user_id"),
        sa.Index("ix_enrollment_codes_expires_at", "expires_at"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    code_hash: Mapped[sha256_hash] = mapped_column(unique=True)
    # First group of the display code, for listing outstanding codes in the UI.
    code_prefix: Mapped[str] = mapped_column(sa.Text, nullable=False)

    # Set when the code is consumed; SET NULL so deleting the device does not
    # erase the record that the code was used.
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )

    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    consumed_ip: Mapped[str | None] = mapped_column(pg.INET, nullable=True)

    created_at: Mapped[ts_now]
