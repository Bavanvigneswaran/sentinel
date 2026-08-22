"""Enrollment codes and agent tokens.

Phase 1 ships these as schema plus service functions; the HTTP surface arrives
in Phase 2 with the agent.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models import User
from app.security.opaque import normalize_code
from app.services import enrollment_service as svc
from app.services.enrollment_service import InvalidEnrollmentCode
from tests.conftest import scoped_session_for


@pytest.fixture
async def user(admin_session) -> User:
    u = User(email="agent-owner@example.com", password_hash="x")
    admin_session.add(u)
    await admin_session.commit()
    return u


async def test_code_is_human_typeable_and_unambiguous(admin_session, user):
    issued = await svc.create_enrollment_code(admin_session, user.id)
    assert len(issued.code) == 14  # XXXX-XXXX-XXXX
    assert issued.code.count("-") == 2
    # Crockford base32 omits I, L, O and U so nothing is misread off a screen.
    assert not set(issued.code.replace("-", "")) & set("ILOU")


async def test_consume_returns_the_owning_user(admin_session, user):
    issued = await svc.create_enrollment_code(admin_session, user.id)
    assert await svc.consume_enrollment_code(admin_session, issued.code) == user.id


async def test_consume_accepts_sloppy_formatting(admin_session, user):
    issued = await svc.create_enrollment_code(admin_session, user.id)
    messy = f"  {issued.code.lower().replace('-', ' ')}  "
    assert await svc.consume_enrollment_code(admin_session, messy) == user.id


async def test_a_code_can_only_be_consumed_once(admin_session, user):
    issued = await svc.create_enrollment_code(admin_session, user.id)
    await svc.consume_enrollment_code(admin_session, issued.code)
    with pytest.raises(InvalidEnrollmentCode):
        await svc.consume_enrollment_code(admin_session, issued.code)


async def test_an_expired_code_is_rejected(admin_session, user):
    issued = await svc.create_enrollment_code(admin_session, user.id, ttl_seconds=-1)
    with pytest.raises(InvalidEnrollmentCode):
        await svc.consume_enrollment_code(admin_session, issued.code)


async def test_an_unknown_code_is_rejected(admin_session, user):
    with pytest.raises(InvalidEnrollmentCode):
        await svc.consume_enrollment_code(admin_session, "XXXX-XXXX-XXXX")


async def test_concurrent_consumption_yields_exactly_one_winner(admin_session, user):
    """Two agents racing on the same code must not both enroll.

    This is why consumption is a single conditional UPDATE ... RETURNING rather
    than a read followed by a write.
    """
    from app.db import AdminSessionLocal

    issued = await svc.create_enrollment_code(admin_session, user.id)

    async def attempt():
        async with AdminSessionLocal() as session:
            try:
                return await svc.consume_enrollment_code(session, issued.code)
            except InvalidEnrollmentCode:
                return None

    results = await asyncio.gather(*(attempt() for _ in range(8)))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1, f"expected exactly one winner, got {len(winners)}"
    assert winners[0] == user.id


async def test_consumption_records_the_device_and_ip(admin_session, user):
    device = await svc.register_device(admin_session, user_id=user.id, name="mac")
    issued = await svc.create_enrollment_code(admin_session, user.id)
    await svc.consume_enrollment_code(
        admin_session, issued.code, device_id=device.id, ip="203.0.113.7"
    )
    row = (
        await admin_session.execute(
            sa.text("SELECT device_id, consumed_ip, consumed_at FROM enrollment_codes")
        )
    ).one()
    assert row.device_id == device.id
    assert str(row.consumed_ip) == "203.0.113.7"
    assert row.consumed_at is not None


async def test_the_plaintext_code_is_never_stored(admin_session, user):
    issued = await svc.create_enrollment_code(admin_session, user.id)
    stored = (
        await admin_session.execute(sa.text("SELECT code_hash, code_prefix FROM enrollment_codes"))
    ).one()
    assert normalize_code(issued.code).encode() not in bytes(stored.code_hash)
    assert stored.code_prefix == issued.code.split("-")[0]


async def test_agent_token_roundtrip(admin_session, user):
    device = await svc.register_device(admin_session, user_id=user.id, name="mac")
    issued = await svc.issue_agent_token(
        admin_session, user_id=user.id, device_id=device.id, name="primary"
    )
    assert issued.token.startswith("sag_")
    assert issued.prefix == issued.token[:12]

    resolved = await svc.resolve_agent_token(admin_session, issued.token)
    assert resolved is not None
    assert resolved.device_id == device.id
    assert resolved.last_used_at is not None


async def test_the_plaintext_agent_token_is_never_stored(admin_session, user):
    device = await svc.register_device(admin_session, user_id=user.id, name="mac")
    issued = await svc.issue_agent_token(admin_session, user_id=user.id, device_id=device.id)
    stored = await admin_session.scalar(sa.text("SELECT token_hash FROM agent_tokens"))
    assert issued.token.encode() not in bytes(stored)


async def test_a_revoked_agent_token_does_not_resolve(admin_session, user):
    device = await svc.register_device(admin_session, user_id=user.id, name="mac")
    issued = await svc.issue_agent_token(admin_session, user_id=user.id, device_id=device.id)
    assert await svc.revoke_agent_token(admin_session, issued.id) is True
    assert await svc.resolve_agent_token(admin_session, issued.token) is None


async def test_an_expired_agent_token_does_not_resolve(admin_session, user):
    device = await svc.register_device(admin_session, user_id=user.id, name="mac")
    issued = await svc.issue_agent_token(
        admin_session,
        user_id=user.id,
        device_id=device.id,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert await svc.resolve_agent_token(admin_session, issued.token) is None


async def test_enrollment_codes_are_tenant_scoped(admin_session, user):
    """RLS covers the Phase 2 tables too, not just the auth ones."""
    other = User(email="other@example.com", password_hash="x")
    admin_session.add(other)
    await admin_session.commit()

    async with scoped_session_for(user.id) as session:
        await svc.create_enrollment_code(session, user.id)

    async with scoped_session_for(other.id) as session:
        count = await session.scalar(sa.text("SELECT count(*) FROM enrollment_codes"))
    assert count == 0


async def test_a_device_cannot_be_created_for_another_tenant(admin_session, user):
    other = User(email="other@example.com", password_hash="x")
    admin_session.add(other)
    await admin_session.commit()

    async with scoped_session_for(user.id) as session:
        with pytest.raises(Exception, match="row-level security"):
            await svc.register_device(session, user_id=other.id, name="planted")


async def test_deleting_a_user_cascades_to_their_devices_and_tokens(admin_session, user):
    device = await svc.register_device(admin_session, user_id=user.id, name="mac")
    await svc.issue_agent_token(admin_session, user_id=user.id, device_id=device.id)
    await svc.create_enrollment_code(admin_session, user.id)

    await admin_session.execute(sa.text("DELETE FROM users WHERE id = :i"), {"i": user.id})
    await admin_session.commit()

    for table in ("devices", "agent_tokens", "enrollment_codes"):
        # `table` comes from a hardcoded tuple, not from input.
        count = await admin_session.scalar(sa.text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        assert count == 0


async def test_device_names_are_unique_per_user_but_not_globally(admin_session, user):
    other = User(email="other@example.com", password_hash="x")
    admin_session.add(other)
    await admin_session.commit()

    await svc.register_device(admin_session, user_id=user.id, name="laptop")
    await svc.register_device(admin_session, user_id=other.id, name="laptop")  # fine

    with pytest.raises(IntegrityError):
        await svc.register_device(admin_session, user_id=user.id, name="laptop")


# --- tenant-consistency constraints (Phase 1 security review) ----------------


async def test_an_agent_token_cannot_claim_another_users_device(admin_session, user):
    """The composite FK is what keeps the denormalized user_id honest.

    Without it a token could name someone else's device while carrying its own
    user_id, and since RLS scopes by that column the token would be visible to
    the wrong tenant.
    """
    other = User(email="other@example.com", password_hash="x")
    admin_session.add(other)
    await admin_session.commit()
    device = await svc.register_device(admin_session, user_id=user.id, name="mac")

    with pytest.raises(IntegrityError):
        await svc.issue_agent_token(admin_session, user_id=other.id, device_id=device.id)
    await admin_session.rollback()


async def test_an_enrollment_code_cannot_be_consumed_onto_another_users_device(
    admin_session, user
):
    other = User(email="other@example.com", password_hash="x")
    admin_session.add(other)
    await admin_session.commit()

    victim_device = await svc.register_device(admin_session, user_id=other.id, name="victim")
    issued = await svc.create_enrollment_code(admin_session, user.id)

    with pytest.raises(InvalidEnrollmentCode):
        await svc.consume_enrollment_code(
            admin_session, issued.code, device_id=victim_device.id
        )

    # And the code must survive unconsumed rather than being burned by the attempt.
    remaining = await admin_session.scalar(
        sa.text("SELECT count(*) FROM enrollment_codes WHERE consumed_at IS NULL")
    )
    assert remaining == 1


async def test_deleting_a_device_keeps_the_enrollment_audit_row(admin_session, user):
    """ON DELETE SET NULL (device_id) nulls only that column, so user_id stays
    NOT NULL and the record that a code was consumed survives."""
    device = await svc.register_device(admin_session, user_id=user.id, name="mac")
    issued = await svc.create_enrollment_code(admin_session, user.id)
    await svc.consume_enrollment_code(admin_session, issued.code, device_id=device.id)

    await admin_session.execute(sa.text("DELETE FROM devices WHERE id = :i"), {"i": device.id})
    await admin_session.commit()

    row = (
        await admin_session.execute(
            sa.text("SELECT device_id, consumed_at, user_id FROM enrollment_codes")
        )
    ).one()
    assert row.device_id is None
    assert row.consumed_at is not None
    assert row.user_id == user.id


async def test_an_unconsumed_code_may_have_no_device(admin_session, user):
    """Composite FKs are MATCH SIMPLE, so a NULL device_id skips the check."""
    issued = await svc.create_enrollment_code(admin_session, user.id)
    assert issued.id is not None
