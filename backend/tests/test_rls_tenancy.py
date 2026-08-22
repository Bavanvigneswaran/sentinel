"""Tenancy isolation, enforced by Postgres rather than by application code."""

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import ProgrammingError

from app.models import Device
from tests.conftest import scoped_session_for


@pytest.fixture
async def two_users_with_devices(admin_session):
    from app.models import User

    a = User(email="a@example.com", password_hash="x")
    b = User(email="b@example.com", password_hash="x")
    admin_session.add_all([a, b])
    await admin_session.flush()
    admin_session.add_all(
        [Device(user_id=a.id, name="laptop-a"), Device(user_id=b.id, name="laptop-b")]
    )
    await admin_session.commit()
    return a, b


async def test_unscoped_session_sees_nothing(app_session, two_users_with_devices):
    """Default deny: a request that reaches the DB without a tenant sees an
    empty database, not everyone's."""
    count = await app_session.scalar(sa.select(sa.func.count()).select_from(Device))
    assert count == 0


async def test_scoped_session_sees_only_its_own_rows(two_users_with_devices):
    a, b = two_users_with_devices

    async with scoped_session_for(a.id) as session:
        names = (await session.scalars(sa.select(Device.name))).all()
    assert sorted(names) == ["laptop-a"]

    async with scoped_session_for(b.id) as session:
        names = (await session.scalars(sa.select(Device.name))).all()
    assert sorted(names) == ["laptop-b"]


async def test_users_table_is_scoped_to_self(two_users_with_devices):
    a, b = two_users_with_devices
    async with scoped_session_for(a.id) as session:
        emails = (await session.scalars(sa.text("SELECT email FROM users"))).all()
    assert emails == ["a@example.com"]


async def test_insert_for_another_tenant_is_rejected(two_users_with_devices):
    """WITH CHECK: an authenticated user must not be able to create rows
    belonging to someone else."""
    a, b = two_users_with_devices
    async with scoped_session_for(a.id) as session:
        session.add(Device(user_id=b.id, name="planted"))
        with pytest.raises(ProgrammingError, match="row-level security"):
            await session.commit()


async def test_update_of_another_tenants_row_affects_nothing(two_users_with_devices):
    a, b = two_users_with_devices
    async with scoped_session_for(a.id) as session:
        result = await session.execute(
            sa.update(Device).where(Device.name == "laptop-b").values(name="hijacked")
        )
        await session.commit()
        assert result.rowcount == 0

    async with scoped_session_for(b.id) as session:
        names = (await session.scalars(sa.select(Device.name))).all()
    assert names == ["laptop-b"]


async def test_delete_of_another_tenants_row_affects_nothing(two_users_with_devices):
    a, b = two_users_with_devices
    async with scoped_session_for(a.id) as session:
        result = await session.execute(sa.delete(Device).where(Device.name == "laptop-b"))
        await session.commit()
        assert result.rowcount == 0

    async with scoped_session_for(b.id) as session:
        assert (await session.scalars(sa.select(Device.name))).all() == ["laptop-b"]


async def test_unknown_tenant_sees_nothing(two_users_with_devices):
    async with scoped_session_for(uuid.uuid4()) as session:
        count = await session.scalar(sa.select(sa.func.count()).select_from(Device))
    assert count == 0
