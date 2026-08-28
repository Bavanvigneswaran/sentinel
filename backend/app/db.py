"""Database engines, session factories, and the tenant-scoping hook.

Two engines by design:

* `engine` connects as the restricted role (`sentinel_app`), which has
  NOBYPASSRLS. Every ordinary request goes through it, so Postgres row-level
  security is a real second line of defence behind the application's own
  user_id filtering.
* `admin_engine` connects as the table owner. It is needed because signup,
  login and refresh must read rows *before* a user identity exists — there is
  no tenant to scope by yet. Only app.services.auth_service may use it, and a
  test enforces that.

Tenancy is carried in a per-transaction GUC, `app.current_user_id`, read by the
`app_current_user_id()` SQL function that every policy calls.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

TENANT_GUC = "app.current_user_id"

# NOTE on connection poolers: `set_config(..., is_local => true)` is
# transaction-scoped, which makes this design compatible with pgbouncer in
# transaction-pooling mode. If pgbouncer is ever introduced, add
# connect_args={"statement_cache_size": 0} and prepared_statement_cache_size=0.
engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=settings.db_pool_pre_ping,
)

admin_engine = create_async_engine(
    settings.admin_database_url,
    pool_size=settings.admin_db_pool_size,
    max_overflow=settings.admin_db_max_overflow,
    pool_pre_ping=settings.db_pool_pre_ping,
)

# expire_on_commit=False is mandatory here: with it on, touching any attribute
# after a commit silently opens a fresh transaction, which would no longer carry
# the tenant GUC.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
AdminSessionLocal = async_sessionmaker(admin_engine, expire_on_commit=False, autoflush=False)


@event.listens_for(Session, "after_begin")
def _apply_tenant_guc(session: Session, transaction, connection) -> None:  # noqa: ANN001
    """Re-apply the tenant GUC at the start of every transaction on a session.

    Registered globally but gated on `session.info["tenant_id"]`, so admin
    sessions are untouched. Doing it here rather than once per request is what
    makes the scoping survive a mid-request commit: the GUC is transaction-local
    and would otherwise vanish, leaving the next statement seeing zero rows.

    `SET LOCAL x = $1` is not usable — SET takes no bind parameters over the
    extended query protocol asyncpg uses. set_config() is an ordinary function
    call and parameterizes normally.
    """
    tenant_id = session.info.get("tenant_id")
    if tenant_id is not None:
        connection.execute(
            text("SELECT set_config(:name, :uid, true)"),
            {"name": TENANT_GUC, "uid": str(tenant_id)},
        )


def scope_to_user(session: AsyncSession, user_id) -> None:  # noqa: ANN001
    """Bind an AsyncSession to a tenant for the rest of its life."""
    session.sync_session.info["tenant_id"] = str(user_id)


async def get_db() -> AsyncIterator[AsyncSession]:
    """An unscoped session on the *restricted* role.

    With no tenant set, `app_current_user_id()` returns NULL and every policy
    predicate evaluates to NULL, so this sees zero rows until it is scoped.
    That default-deny is the point.

    Routes commit explicitly. Committing on dependency exit would surface a
    UniqueViolation after the response was already built, making a clean 409
    impossible.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_unscoped_session() -> AsyncIterator[AsyncSession]:
    """A session on the OWNER role. RLS does not apply.

    Deliberately named to read as dangerous. Only the pre-authentication paths
    in app.services.auth_service may use this; everything else must go through
    get_db() plus scope_to_user(). tests/test_unscoped_import_guard.py enforces it.
    """
    async with AdminSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engines() -> None:
    await engine.dispose()
    await admin_engine.dispose()
