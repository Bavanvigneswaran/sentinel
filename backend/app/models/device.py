from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, in_check, uuid_pk

PLATFORMS = ("desktop", "android")
DEVICE_STATUSES = ("pending", "online", "offline")


class Device(TimestampMixin, Base):
    __tablename__ = "devices"
    __table_args__ = (
        sa.CheckConstraint(in_check("platform", PLATFORMS), name="platform"),
        sa.CheckConstraint(in_check("status", DEVICE_STATUSES), name="status"),
        # Unique among *live* devices only: a soft delete is how metric rows
        # keep a valid FK, and it has no business also meaning "this name is
        # taken forever". See migration 0013.
        sa.Index(
            "uq_devices_user_id_name_live",
            "user_id",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        # Redundant against the primary key, but it gives child tables a
        # composite FK target so a row can never reference a device owned by a
        # different user. See agent_tokens and enrollment_codes.
        sa.UniqueConstraint("id", "user_id", name="uq_devices_id_user_id"),
        # Also serves the RLS predicate `user_id = app_current_user_id()`.
        sa.Index("ix_devices_user_id", "user_id"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(sa.Text, nullable=False)

    # Host metadata, all reported by the agent at enrollment (Phase 2). Nullable
    # because nothing is known until an agent actually connects — never synthesised.
    hostname: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    os: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    os_version: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    kernel_version: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    arch: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    cpu_cores: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    total_memory_bytes: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    agent_version: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    platform: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'desktop'")
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'pending'")
    )

    last_seen_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    enrolled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    # Soft delete, so Phase 2's metrics hypertable never ends up with a dangling FK.
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
