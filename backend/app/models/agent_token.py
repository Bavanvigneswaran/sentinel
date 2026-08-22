from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, sha256_hash, ts_now, uuid_pk

AGENT_TOKEN_SCHEME = "sag_"  # noqa: S105 — a scheme prefix, not a credential


class AgentToken(Base):
    """A long-lived opaque credential scoped to exactly one device.

    Agents never see the user's password; they exchange a one-time enrollment
    code for one of these. Stored as sha256 at rest and revocable from the UI.
    """

    __tablename__ = "agent_tokens"
    __table_args__ = (
        # user_id is denormalized from devices so the RLS policy is a single
        # indexed predicate rather than a join.
        sa.Index("ix_agent_tokens_user_id", "user_id"),
        sa.Index("ix_agent_tokens_device_id", "device_id"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )

    token_hash: Mapped[sha256_hash] = mapped_column(unique=True)

    # First few characters, e.g. "sag_1a2b3c4d" — shown in the UI so a user can
    # tell two tokens apart. Not secret and not sufficient to authenticate.
    token_prefix: Mapped[str] = mapped_column(sa.Text, nullable=False)

    name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    created_at: Mapped[ts_now]
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
