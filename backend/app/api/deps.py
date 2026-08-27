"""Request dependencies: authentication and tenant scoping.

The ordering matters. The access JWT is decoded with no database access at all,
so `sub` yields a cryptographically verified user id *before* the first row is
read — which is what resolves the otherwise circular problem of needing the
tenant GUC to read the user, and the user to know the tenant.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, get_unscoped_session, scope_to_user
from app.models import User
from app.security.tokens import AccessClaims, InvalidAccessToken, decode_access_token

_bearer = HTTPBearer(auto_error=False)

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_access_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AccessClaims:
    if credentials is None or not credentials.credentials:
        raise CREDENTIALS_ERROR
    try:
        return decode_access_token(credentials.credentials)
    except InvalidAccessToken as exc:
        raise CREDENTIALS_ERROR from exc


async def get_tenant_session(
    claims: Annotated[AccessClaims, Depends(get_access_claims)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AsyncSession:
    """A restricted-role session bound to the caller's tenant.

    FastAPI caches dependencies per request, so a route declaring both this and
    get_current_user receives the same AsyncSession instance.
    """
    scope_to_user(session, claims.sub)
    return session


async def _resolve_user(claims: AccessClaims, session: AsyncSession) -> User | None:
    user = await session.get(User, claims.sub)

    if user is None or not user.is_active:
        return None

    # A password change invalidates every access token issued before it, without
    # needing a denylist. One second of slack absorbs timestamp rounding.
    if claims.issued_at.timestamp() < user.password_changed_at.timestamp() - 1:
        return None

    return user


async def get_current_user(
    claims: Annotated[AccessClaims, Depends(get_access_claims)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> User:
    user = await _resolve_user(claims, session)
    if user is None:
        raise CREDENTIALS_ERROR
    return user


async def get_current_user_or_none(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    """Like `get_current_user`, but a missing or invalid bearer yields `None`
    instead of a 401.

    For the one route where a bearer token is not the only valid credential —
    an agent-build download can instead present a ticket (see
    `app/services/download_tickets.py`) — and the route itself decides what to
    do when neither is present.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        claims = decode_access_token(credentials.credentials)
    except InvalidAccessToken:
        return None
    scope_to_user(session, claims.sub)
    return await _resolve_user(claims, session)


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentUserOrNone = Annotated[User | None, Depends(get_current_user_or_none)]
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
UnscopedSession = Annotated[AsyncSession, Depends(get_unscoped_session)]
