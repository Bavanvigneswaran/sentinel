"""Access-token (JWT) issue and verify.

Only the short-lived access token is a JWT. Refresh tokens are opaque and live
in the database, so they can be revoked — a stateless refresh JWT cannot be.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.config import get_settings

ACCESS_TOKEN_TYPE = "access"  # noqa: S105 — a claim value, not a credential


class InvalidAccessToken(Exception):
    """Raised for any failure: expired, tampered, wrong audience, wrong type."""


@dataclass(frozen=True, slots=True)
class AccessClaims:
    sub: uuid.UUID
    jti: uuid.UUID
    issued_at: datetime
    expires_at: datetime


def issue_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    """Returns (token, expires_in_seconds)."""
    settings = get_settings()
    now = datetime.now(UTC)
    ttl = settings.jwt_access_ttl_seconds
    payload = {
        "sub": str(user_id),
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": str(uuid.uuid4()),
        "typ": ACCESS_TOKEN_TYPE,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, ttl


def decode_access_token(token: str) -> AccessClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            # An explicit algorithm list is the defence against `alg: none` and
            # RS256-to-HS256 confusion. Never widen this.
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "nbf", "sub", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidAccessToken(str(exc)) from exc

    # PyJWT does not validate `typ`. Without this check a refresh-typed token
    # would be accepted on the access path.
    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        raise InvalidAccessToken("wrong token type")

    try:
        return AccessClaims(
            sub=uuid.UUID(payload["sub"]),
            jti=uuid.UUID(payload["jti"]),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidAccessToken("malformed claims") from exc
