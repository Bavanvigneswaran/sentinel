"""Device management and agent enrollment.

Two audiences on one router:

* `/devices` and `/enrollment-codes` are the user's, authenticated with the
  access JWT and scoped by RLS.
* `/enroll` is the agent's, authenticated by the one-time code itself. It is
  the only unauthenticated write in the system, which is why it is rate limited
  and why the code is single-use, short-lived, and consumed atomically.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import CurrentUser, TenantSession, UnscopedSession
from app.api.ratelimit import RateLimit
from app.models import AgentToken, Device
from app.schemas.devices import (
    AgentTokenOut,
    DeviceCreate,
    DeviceOut,
    EnrollmentCodeCreate,
    EnrollmentCodeOut,
    EnrollRequest,
    EnrollResponse,
)
from app.services import enrollment_service as svc
from app.services.enrollment_service import InvalidEnrollmentCode

router = APIRouter(tags=["devices"])


async def _unique_device_name(session, user_id: uuid.UUID, name: str) -> str:  # noqa: ANN001
    """Device names are unique per user, and two machines can share a hostname.

    Rather than failing an agent that is only trying to enroll, disambiguate
    with a numeric suffix. The user can rename it afterwards.
    """
    taken = set(
        (
            await session.scalars(
                sa.select(Device.name).where(
                    Device.user_id == user_id, Device.name.like(f"{name}%")
                )
            )
        ).all()
    )
    if name not in taken:
        return name
    for suffix in range(2, 1000):
        candidate = f"{name}-{suffix}"
        if candidate not in taken:
            return candidate
    # Astronomically unlikely; a uuid fragment beats raising here.
    return f"{name}-{uuid.uuid4().hex[:6]}"




# The one unauthenticated write in the system. 60 bits of entropy behind a
# 15-minute window is strong, but an unthrottled endpoint invites guessing.
_enroll_limit = RateLimit("enroll", limit=10, window=3600, key="ip")
_code_limit = RateLimit("enrollment_code", limit=20, window=3600, key="ip")


@router.get("/devices", response_model=list[DeviceOut])
async def list_devices(user: CurrentUser, session: TenantSession) -> list[Device]:
    rows = await session.scalars(
        sa.select(Device).where(Device.deleted_at.is_(None)).order_by(Device.created_at)
    )
    return list(rows)


@router.post("/devices", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def create_device(
    payload: DeviceCreate, user: CurrentUser, session: TenantSession
) -> Device:
    device = Device(user_id=user.id, name=payload.name, platform=payload.platform)
    session.add(device)
    try:
        await session.commit()
    except sa.exc.IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a device with that name",
        ) from exc
    return device


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: uuid.UUID, user: CurrentUser, session: TenantSession
) -> None:
    """Soft delete, so Phase 2's metric rows keep a valid foreign key.

    Revokes every agent token for the device in the same transaction: a soft
    delete that leaves a working credential behind is not a delete.
    """
    result = await session.execute(
        sa.update(Device)
        .where(Device.id == device_id, Device.deleted_at.is_(None))
        .values(deleted_at=sa.func.now(), status="offline")
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    await session.execute(
        sa.update(AgentToken)
        .where(AgentToken.device_id == device_id, AgentToken.revoked_at.is_(None))
        .values(revoked_at=sa.func.now(), revoked_reason="admin")
    )
    await session.commit()


@router.get("/devices/{device_id}/tokens", response_model=list[AgentTokenOut])
async def list_agent_tokens(
    device_id: uuid.UUID, user: CurrentUser, session: TenantSession
) -> list[AgentToken]:
    rows = await session.scalars(
        sa.select(AgentToken)
        .where(AgentToken.device_id == device_id)
        .order_by(AgentToken.created_at)
    )
    return list(rows)


@router.delete("/agent-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_agent_token(
    token_id: uuid.UUID, user: CurrentUser, session: TenantSession
) -> None:
    result = await session.execute(
        sa.update(AgentToken)
        .where(AgentToken.id == token_id, AgentToken.revoked_at.is_(None))
        .values(revoked_at=sa.func.now(), revoked_reason="admin")
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    await session.commit()


@router.post(
    "/enrollment-codes",
    response_model=EnrollmentCodeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_code_limit)],
)
async def create_enrollment_code(
    payload: EnrollmentCodeCreate, user: CurrentUser, session: TenantSession
) -> EnrollmentCodeOut:
    """Mint a one-time code. The plaintext is returned once and never stored."""
    if payload.device_id is not None:
        # RLS already scopes this, so a miss means "not yours" or "not there" —
        # indistinguishable, which is what we want.
        exists = await session.scalar(
            sa.select(sa.func.count())
            .select_from(Device)
            .where(Device.id == payload.device_id, Device.deleted_at.is_(None))
        )
        if not exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Device not found"
            )

    issued = await svc.create_enrollment_code(
        session, user.id, ttl_seconds=payload.ttl_seconds, device_id=payload.device_id
    )
    return EnrollmentCodeOut(id=issued.id, code=issued.code, expires_at=issued.expires_at)


@router.post(
    "/enroll",
    response_model=EnrollResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_enroll_limit)],
)
async def enroll(
    payload: EnrollRequest,
    request: Request,
    session: UnscopedSession,
) -> EnrollResponse:
    """Exchange a one-time code for a long-lived agent token.

    Unauthenticated by necessity: the agent has no user identity yet, and it
    must never see the user's password. The code is the credential — single
    use, short lived, and consumed by one atomic statement so two agents racing
    on the same code cannot both enroll.
    """
    ip = request.client.host if request.client else None

    try:
        consumed = await svc.consume_enrollment_code(session, payload.code, ip=ip)
    except InvalidEnrollmentCode as exc:
        # Invalid, expired and already-used are one response on purpose.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired enrollment code",
        ) from exc

    if consumed.device_id is not None:
        # The code was minted against an existing device: re-enrolment. Reuse
        # it so the machine keeps its metric history.
        device = await session.get(Device, consumed.device_id)
        if device is None:  # deleted between minting and enrolling
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired enrollment code",
            )
    else:
        device = await svc.register_device(
            session,
            user_id=consumed.user_id,
            name=await _unique_device_name(session, consumed.user_id, payload.device_name),
            platform=payload.platform,
        )
        await svc.link_code_to_device(session, consumed.code_id, device.id)
    issued = await svc.issue_agent_token(
        session, user_id=consumed.user_id, device_id=device.id, name=payload.device_name
    )
    await session.commit()

    return EnrollResponse(device_id=device.id, agent_token=issued.token)


__all__ = ["router", "Annotated"]
