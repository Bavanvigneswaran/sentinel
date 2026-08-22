"""Enrollment codes and agent tokens.

Phase 1 provides the schema and the logic; the HTTP surface arrives in Phase 2
with the agent itself, and the UI in Phase 3.

Every function here takes a tenant-scoped session — RLS makes cross-tenant reads
return nothing and cross-tenant writes fail — except consume_enrollment_code(),
which is called by an agent that has no user identity yet.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentToken, Device, EnrollmentCode
from app.models.enrollment_code import ENROLLMENT_CODE_TTL_SECONDS
from app.security.opaque import new_agent_token, new_enrollment_code, normalize_code, sha256_bytes


class InvalidEnrollmentCode(Exception):
    """Invalid, expired, or already consumed — deliberately indistinguishable."""


@dataclass(frozen=True, slots=True)
class IssuedEnrollmentCode:
    id: uuid.UUID
    code: str  # shown to the user exactly once
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedAgentToken:
    id: uuid.UUID
    token: str  # returned to the agent exactly once
    prefix: str
    device_id: uuid.UUID


async def create_enrollment_code(
    session: AsyncSession, user_id: uuid.UUID, *, ttl_seconds: int = ENROLLMENT_CODE_TTL_SECONDS
) -> IssuedEnrollmentCode:
    display, prefix, code_hash = new_enrollment_code()
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)

    row = EnrollmentCode(
        user_id=user_id, code_hash=code_hash, code_prefix=prefix, expires_at=expires_at
    )
    session.add(row)
    await session.commit()
    return IssuedEnrollmentCode(id=row.id, code=display, expires_at=expires_at)


async def consume_enrollment_code(
    session: AsyncSession, code: str, *, device_id: uuid.UUID | None = None, ip: str | None = None
) -> uuid.UUID:
    """Atomically claim a code and return the owning user_id.

    A single conditional UPDATE ... RETURNING, never a read followed by a write:
    two agents racing on the same code must produce exactly one winner. Zero rows
    back means invalid, expired, or already used, and the caller cannot tell
    which.
    """
    code_hash = sha256_bytes(normalize_code(code))

    result = await session.execute(
        sa.update(EnrollmentCode)
        .where(
            EnrollmentCode.code_hash == code_hash,
            EnrollmentCode.consumed_at.is_(None),
            EnrollmentCode.expires_at > sa.func.now(),
        )
        .values(consumed_at=sa.func.now(), device_id=device_id, consumed_ip=ip)
        .returning(EnrollmentCode.user_id)
    )
    user_id = result.scalar_one_or_none()
    await session.commit()

    if user_id is None:
        raise InvalidEnrollmentCode
    return user_id


async def issue_agent_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    name: str | None = None,
    expires_at: datetime | None = None,
) -> IssuedAgentToken:
    """Mint an opaque token scoped to one device. The plaintext is returned once
    and never stored."""
    token, prefix, token_hash = new_agent_token()

    row = AgentToken(
        user_id=user_id,
        device_id=device_id,
        token_hash=token_hash,
        token_prefix=prefix,
        name=name,
        expires_at=expires_at,
    )
    session.add(row)
    await session.commit()
    return IssuedAgentToken(id=row.id, token=token, prefix=prefix, device_id=device_id)


async def resolve_agent_token(session: AsyncSession, token: str) -> AgentToken | None:
    """Look up a live agent token by its plaintext, updating last_used_at.

    Called on the ingest path (Phase 2) with an unscoped session — an agent
    presents a token, not a user identity.
    """
    now = datetime.now(UTC)
    row = await session.scalar(
        sa.select(AgentToken).where(
            AgentToken.token_hash == sha256_bytes(token),
            AgentToken.revoked_at.is_(None),
            sa.or_(AgentToken.expires_at.is_(None), AgentToken.expires_at > now),
        )
    )
    if row is not None:
        row.last_used_at = now
        await session.commit()
    return row


async def revoke_agent_token(
    session: AsyncSession, token_id: uuid.UUID, *, reason: str = "revoked"
) -> bool:
    result = await session.execute(
        sa.update(AgentToken)
        .where(AgentToken.id == token_id, AgentToken.revoked_at.is_(None))
        .values(revoked_at=sa.func.now(), revoked_reason=reason)
    )
    await session.commit()
    return result.rowcount > 0


async def register_device(
    session: AsyncSession, *, user_id: uuid.UUID, name: str, **metadata
) -> Device:
    device = Device(user_id=user_id, name=name, **metadata)
    session.add(device)
    await session.commit()
    return device
