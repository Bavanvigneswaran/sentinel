"""Authentication logic.

Pure functions over an AsyncSession — no Request, no Response — so the rotation
and reuse-detection rules can be unit-tested without going through HTTP.

This is the ONLY module permitted to use the owner-role session. Signup, login
and refresh must read rows before a user identity exists, so there is no tenant
to scope by; every other part of the application goes through the restricted
role with RLS applied. tests/test_unscoped_import_guard.py enforces that.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.defaults import ensure_default_rules
from app.config import get_settings
from app.models import RefreshToken, User
from app.security.opaque import new_refresh_token, sha256_bytes
from app.security.passwords import hash_password, verify_dummy_password, verify_password
from app.security.tokens import issue_access_token


class EmailAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class InvalidRefreshToken(Exception):
    """The presented refresh token is unusable. Always maps to a 401."""


@dataclass(frozen=True, slots=True)
class IssuedSession:
    user: User
    access_token: str
    expires_in: int
    refresh_secret: str


def normalize_email(email: str) -> str:
    """Emails are stored lowercased and stripped, so Foo@X.com and foo@x.com
    are the same account."""
    return email.strip().lower()


async def signup(
    session: AsyncSession, *, email: str, password: str, display_name: str | None = None
) -> User:
    normalized = normalize_email(email)
    user = User(
        email=normalized,
        password_hash=await hash_password(password),
        display_name=display_name.strip() if display_name else None,
    )
    session.add(user)
    try:
        # Flushed, not committed, so the default rules below land in the same
        # transaction as the account itself: an account that exists but has no
        # detection configured is exactly the state this seeding exists to
        # prevent, and a commit in between would create it for real if the
        # second half failed.
        await session.flush()
        await ensure_default_rules(session, user.id)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise EmailAlreadyRegistered(normalized) from exc
    return user


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    """Verify credentials, or raise InvalidCredentials.

    The unknown-email path still performs a full argon2 verification against a
    dummy hash, so response timing does not distinguish "no such user" from
    "wrong password".
    """
    normalized = normalize_email(email)
    user = await session.scalar(sa.select(User).where(User.email == normalized))

    if user is None:
        await verify_dummy_password(password)
        raise InvalidCredentials

    if not await verify_password(user.password_hash, password):
        raise InvalidCredentials

    if not user.is_active:
        raise InvalidCredentials

    user.last_login_at = datetime.now(UTC)
    await session.commit()
    return user


async def issue_session(
    session: AsyncSession,
    user: User,
    *,
    family_id: uuid.UUID | None = None,
    parent_id: uuid.UUID | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
) -> IssuedSession:
    """Mint an access token plus a new refresh token row.

    `family_id` is None for a fresh login and carried through for a rotation.
    """
    settings = get_settings()
    secret, token_hash = new_refresh_token()

    row = RefreshToken(
        family_id=family_id or uuid.uuid4(),
        user_id=user.id,
        token_hash=token_hash,
        parent_id=parent_id,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.jwt_refresh_ttl_seconds),
        user_agent=user_agent[:512] if user_agent else None,
        ip=ip,
    )
    session.add(row)
    await session.commit()

    access_token, expires_in = issue_access_token(user.id)
    return IssuedSession(
        user=user, access_token=access_token, expires_in=expires_in, refresh_secret=secret
    )


async def revoke_family(
    session: AsyncSession, family_id: uuid.UUID, reason: str, *, commit: bool = True
) -> int:
    """Revoke every still-live token in a family. Returns the number affected."""
    now = datetime.now(UTC)
    result = await session.execute(
        sa.update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )
    if commit:
        await session.commit()
    return result.rowcount


async def rotate_refresh(
    session: AsyncSession,
    presented_secret: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> IssuedSession:
    """Exchange a refresh token for a new one, detecting replay.

    SELECT ... FOR UPDATE serialises concurrent exchanges of the same token: the
    loser observes used_at already set and correctly reports reuse. That is the
    right behaviour for a stolen token and the wrong behaviour for a
    double-submitting SPA, which is why the frontend must single-flight its
    refresh calls.
    """
    token_hash = sha256_bytes(presented_secret)
    now = datetime.now(UTC)

    row = await session.scalar(
        sa.select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    )

    if row is None:
        # Nothing to revoke: an unknown token belongs to no known family.
        raise InvalidRefreshToken("unknown token")

    if row.used_at is not None:
        # Replay of an already-exchanged token. Either the cookie was stolen and
        # the thief got here second, or it was stolen and used first and this is
        # the legitimate user. We cannot tell, so we revoke the whole chain and
        # force a fresh login.
        await revoke_family(session, row.family_id, "reuse_detected")
        raise InvalidRefreshToken("token reuse detected")

    if row.revoked_at is not None:
        # Already revoked by logout or by an earlier reuse. Do not escalate:
        # re-revoking a dead family tells us nothing new.
        raise InvalidRefreshToken("token revoked")

    if row.expires_at <= now:
        row.revoked_at = now
        row.revoked_reason = "expired"
        await session.commit()
        raise InvalidRefreshToken("token expired")

    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        await revoke_family(session, row.family_id, "admin")
        raise InvalidRefreshToken("user unavailable")

    row.used_at = now
    row.revoked_at = now
    row.revoked_reason = "rotated"
    await session.flush()

    return await issue_session(
        session,
        user,
        family_id=row.family_id,
        parent_id=row.id,
        user_agent=user_agent,
        ip=ip,
    )


async def logout(session: AsyncSession, presented_secret: str | None) -> None:
    """Revoke the presented token's whole family. Never raises.

    Logout is idempotent by design: a logout that can fail is a bug, and there
    is nothing useful to tell a caller who presents an unknown cookie.
    """
    if not presented_secret:
        return
    row = await session.scalar(
        sa.select(RefreshToken).where(RefreshToken.token_hash == sha256_bytes(presented_secret))
    )
    if row is not None:
        await revoke_family(session, row.family_id, "logout")
