from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, sha256_hash, ts_now, uuid_pk

# Why sha256 and not argon2: the refresh secret is 256 bits from
# secrets.token_urlsafe(32). Argon2 exists to make *low-entropy* secrets expensive
# to guess; a 256-bit random value is not guessable at any hash speed, and argon2
# would add ~80ms to every refresh.
REVOKED_REASONS = (
    "rotated",
    "logout",
    "reuse_detected",
    "password_change",
    "expired",
    "admin",
)

_REVOKED_REASON_SQL = "revoked_reason IS NULL OR revoked_reason IN ({})".format(
    ", ".join(f"'{r}'" for r in REVOKED_REASONS)
)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        sa.CheckConstraint(_REVOKED_REASON_SQL, name="revoked_reason"),
        sa.Index("ix_refresh_tokens_family_id", "family_id"),
        sa.Index("ix_refresh_tokens_user_id", "user_id"),
        sa.Index("ix_refresh_tokens_expires_at", "expires_at"),
    )

    id: Mapped[uuid_pk]

    # Constant across an entire rotation chain. This is the unit of revocation:
    # detecting reuse of any one token kills every token in the family.
    family_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    token_hash: Mapped[sha256_hash] = mapped_column(unique=True)

    # Audit chain only — self-referential, so alembic emits it as a separate
    # create_foreign_key. SET NULL keeps history readable if a row is pruned.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )

    issued_at: Mapped[ts_now]
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)

    # Set the moment the token is exchanged. Presented again afterwards = reuse.
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    user_agent: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(pg.INET, nullable=True)

    @property
    def is_usable(self) -> bool:
        return self.used_at is None and self.revoked_at is None
