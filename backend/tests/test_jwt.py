import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import get_settings
from app.security.tokens import InvalidAccessToken, decode_access_token, issue_access_token


def _encode(payload: dict, secret: str | None = None, algorithm: str = "HS256") -> str:
    s = get_settings()
    return jwt.encode(payload, secret or s.jwt_secret, algorithm=algorithm)


def _base_payload(**overrides) -> dict:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid.uuid4()),
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=15),
        "jti": str(uuid.uuid4()),
        "typ": "access",
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
    }
    payload.update(overrides)
    return payload


def test_roundtrip():
    user_id = uuid.uuid4()
    token, ttl = issue_access_token(user_id)
    claims = decode_access_token(token)
    assert claims.sub == user_id
    assert ttl == get_settings().jwt_access_ttl_seconds


def test_expired_is_rejected():
    now = datetime.now(UTC)
    token = _encode(_base_payload(iat=now - timedelta(hours=2), exp=now - timedelta(hours=1)))
    with pytest.raises(InvalidAccessToken):
        decode_access_token(token)


def test_wrong_secret_is_rejected():
    token = _encode(_base_payload(), secret="a-completely-different-secret")
    with pytest.raises(InvalidAccessToken):
        decode_access_token(token)


def test_alg_none_is_rejected():
    """The classic JWT forgery. An explicit algorithms= list is what stops it."""
    payload = _base_payload()
    payload["iat"] = int(payload["iat"].timestamp())
    payload["nbf"] = int(payload["nbf"].timestamp())
    payload["exp"] = int(payload["exp"].timestamp())
    forged = jwt.encode(payload, key="", algorithm="none")
    with pytest.raises(InvalidAccessToken):
        decode_access_token(forged)


def test_wrong_audience_is_rejected():
    with pytest.raises(InvalidAccessToken):
        decode_access_token(_encode(_base_payload(aud="someone-elses-app")))


def test_wrong_issuer_is_rejected():
    with pytest.raises(InvalidAccessToken):
        decode_access_token(_encode(_base_payload(iss="not-sentinel")))


def test_refresh_typed_token_is_rejected_on_the_access_path():
    """PyJWT does not check `typ`; this asserts we do it ourselves."""
    with pytest.raises(InvalidAccessToken):
        decode_access_token(_encode(_base_payload(typ="refresh")))


def test_missing_required_claim_is_rejected():
    payload = _base_payload()
    del payload["jti"]
    with pytest.raises(InvalidAccessToken):
        decode_access_token(_encode(payload))


def test_tampered_signature_is_rejected():
    token, _ = issue_access_token(uuid.uuid4())
    head, body, sig = token.split(".")
    with pytest.raises(InvalidAccessToken):
        decode_access_token(f"{head}.{body}.{sig[:-4]}AAAA")
