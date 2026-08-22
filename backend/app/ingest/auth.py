"""Agent authentication for the ingest socket.

Agents present an opaque bearer token, never a user password and never a JWT.
The token is sha256-hashed at rest, scoped to exactly one device, and revocable
from the UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentToken, Device
from app.security.opaque import AGENT_TOKEN_SCHEME, sha256_bytes

logger = logging.getLogger(__name__)

#: Only bump last_used_at this often. It is a liveness hint, not an audit trail,
#: and writing it on every 10s push would be a pointless row update per agent.
LAST_USED_REFRESH_SECONDS = 300


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    token_id: UUID
    device_id: UUID
    user_id: UUID


def extract_token(header: str | None) -> str | None:
    """Pull the token out of an `Authorization: Bearer ...` header."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    value = value.strip()
    # Cheap shape check before hashing; a token that is not ours cannot match.
    if not value.startswith(AGENT_TOKEN_SCHEME):
        return None
    return value


async def authenticate_agent(session: AsyncSession, token: str) -> AgentIdentity | None:
    """Resolve a token to its device and owner, or None.

    Returns None for unknown, revoked, expired, and soft-deleted-device cases
    alike — an agent learns only that it may not connect.
    """
    now = datetime.now(UTC)

    row = (
        await session.execute(
            sa.select(
                AgentToken.id,
                AgentToken.device_id,
                AgentToken.user_id,
                AgentToken.last_used_at,
            )
            .join(Device, Device.id == AgentToken.device_id)
            .where(
                AgentToken.token_hash == sha256_bytes(token),
                AgentToken.revoked_at.is_(None),
                sa.or_(AgentToken.expires_at.is_(None), AgentToken.expires_at > now),
                Device.deleted_at.is_(None),
            )
        )
    ).one_or_none()

    if row is None:
        return None

    stale = row.last_used_at is None or (now - row.last_used_at).total_seconds() > (
        LAST_USED_REFRESH_SECONDS
    )
    if stale:
        await session.execute(
            sa.update(AgentToken).where(AgentToken.id == row.id).values(last_used_at=now)
        )
        await session.commit()

    return AgentIdentity(token_id=row.id, device_id=row.device_id, user_id=row.user_id)
