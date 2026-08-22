"""The tenant GUC is transaction-local. These tests pin down the two ways that
could go wrong: losing scoping after a commit, and leaking it to the next user
of a pooled connection.
"""

import sqlalchemy as sa

from app.models import Device, User
from tests.conftest import scoped_session_for


async def test_scoping_survives_a_commit(admin_session):
    """A mid-request commit must not silently unscope the session.

    set_config(..., true) unwinds at commit, so without the after_begin listener
    re-applying it the next statement would see zero rows — the kind of bug that
    only shows up under a write-then-read handler.
    """
    a = User(email="a@example.com", password_hash="x")
    admin_session.add(a)
    await admin_session.flush()
    admin_session.add(Device(user_id=a.id, name="laptop-a"))
    await admin_session.commit()

    async with scoped_session_for(a.id) as session:
        assert (await session.scalars(sa.select(Device.name))).all() == ["laptop-a"]
        session.add(Device(user_id=a.id, name="second"))
        await session.commit()  # GUC unwinds here; the listener must reapply it
        names = sorted((await session.scalars(sa.select(Device.name))).all())
        assert names == ["laptop-a", "second"], "scoping was lost after commit"


async def test_guc_does_not_leak_to_the_next_session(admin_session):
    """A pooled connection returned after a scoped request must come back clean."""
    a = User(email="a@example.com", password_hash="x")
    admin_session.add(a)
    await admin_session.flush()
    admin_session.add(Device(user_id=a.id, name="laptop-a"))
    await admin_session.commit()

    async with scoped_session_for(a.id) as session:
        assert (await session.scalars(sa.select(Device.name))).all() == ["laptop-a"]

    # Same pool, fresh session, no tenant: must be default-deny again.
    from app.db import SessionLocal

    async with SessionLocal() as session:
        count = await session.scalar(sa.select(sa.func.count()).select_from(Device))
        assert count == 0, "tenant GUC leaked across pooled connections"
