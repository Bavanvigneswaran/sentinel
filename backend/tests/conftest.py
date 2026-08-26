"""Test harness.

The suite owns its database *and* its role: it drops and recreates
`sentinel_test` on the same Timescale container, migrates it, and points the app
engines at it as `sentinel_app_test` — never as the `sentinel_app` a real
deployment uses. Migration 0001 takes the role name from APP_DB_ROLE and creates
it when missing, guarded by IF NOT EXISTS because roles are cluster-global; the
per-database grants are re-applied on every run regardless.

That separation is load-bearing rather than tidy. A ROLE is cluster-wide and a
GRANT is per-database, so a test database on a shared cluster is not isolated
from the credential it logs in with. While the two shared one, rotating the
deployment's password with ALTER ROLE — the correct way to rotate it — broke the
entire suite, and the only repairs on offer were to commit a live credential to
the repo or to hand-patch .env.test after every rotation. `_no_shared_credential`
below is what stops the two quietly growing back together.

Environment MUST be configured before anything under `app.` is imported: Settings
is lru_cached, and app.db builds its engines at import time.
"""

from __future__ import annotations

import os
import pathlib

os.environ.setdefault("SENTINEL_ENV_FILE", str(pathlib.Path(__file__).parents[1] / ".env.test"))

import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from app.config import get_settings  # noqa: E402

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
TENANT_TABLES = ("users", "devices", "refresh_tokens", "agent_tokens", "enrollment_codes")


def _server_url(url: str, database: str) -> str:
    # render_as_string(hide_password=False) is required: str(URL) masks the
    # password as *** and the connection then fails with a confusing auth error.
    return sa.make_url(url).set(database=database).render_as_string(hide_password=False)


def _no_shared_credential(settings) -> None:  # noqa: ANN001
    """Refuse to run as a role that is not exclusively the suite's.

    The database-name check above has a twin, and for a long time only one of
    them existed. `sentinel_test` being a separate *database* says nothing about
    the *role*: roles live in the cluster, so pointing the suite at a deployment's
    login means every credential rotation over there is a broken test run here,
    and every fix is either a committed secret or a manual edit.

    The second assertion is the one that catches a half-finished change to
    .env.test: migrations grant to APP_DB_ROLE while the engine connects as
    whoever DATABASE_URL names, so editing one and not the other yields a suite
    that grants the test role and then logs in as the production one — which
    fails as an authentication error naming neither setting.
    """
    role = settings.app_db_role
    assert role.endswith("_test"), (
        f"the suite must have its own database role, not {role!r} — a role is "
        f"cluster-wide, so sharing one with a real deployment means its next "
        f"password rotation breaks this suite. See backend/.env.test."
    )
    login = sa.make_url(settings.database_url).username
    assert login == role, (
        f"DATABASE_URL connects as {login!r} but APP_DB_ROLE is {role!r}; "
        f"migrations would grant one role and the engine log in as another."
    )


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Drop, recreate, and migrate the test database once per session."""
    settings = get_settings()
    assert settings.environment == "test", "refusing to run tests against a non-test environment"

    target = sa.make_url(settings.admin_database_url).database
    assert target and target.endswith("_test"), f"unsafe test database name: {target!r}"
    _no_shared_credential(settings)

    import asyncio

    async def _recreate() -> None:
        # CREATE/DROP DATABASE cannot run inside a transaction.
        engine = create_async_engine(
            _server_url(settings.admin_database_url, "postgres"),
            isolation_level="AUTOCOMMIT",
        )
        async with engine.connect() as conn:
            await conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": target},
            )
            await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{target}"'))
            await conn.execute(sa.text(f'CREATE DATABASE "{target}"'))
        await engine.dispose()

    asyncio.run(_recreate())

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
async def admin_engine():
    """Owner-role engine. RLS does not apply — used for setup and assertions."""
    from app.db import admin_engine as eng

    yield eng


@pytest.fixture(autouse=True)
async def _clean_rate_limits() -> AsyncIterator[None]:
    """Give every test a fresh rate-limit budget.

    Rate limiting stays *enabled* in tests so the dependency is genuinely
    exercised, but the counters live in Redis and would otherwise accumulate
    across the suite — signup is 5/hour per IP, and every test shares one client
    address.
    """
    from app.api.ratelimit import get_redis

    await get_redis().flushdb()
    yield


@pytest.fixture(autouse=True)
async def _clean_tables(admin_engine) -> AsyncIterator[None]:
    """Truncate between tests.

    Deliberately not the nested-transaction/savepoint pattern: the tenant GUC is
    transaction-local and unwinds at transaction end rather than savepoint
    release, so wrapping tests in an outer transaction would leak scoping across
    "commits" and stop the rotation and reuse tests from exercising real
    semantics.
    """
    yield
    async with admin_engine.begin() as conn:
        await conn.execute(
            sa.text(f"TRUNCATE {', '.join(TENANT_TABLES)} RESTART IDENTITY CASCADE")
        )


@pytest.fixture
async def admin_session(admin_engine) -> AsyncIterator[AsyncSession]:
    from app.db import AdminSessionLocal

    async with AdminSessionLocal() as session:
        yield session


@pytest.fixture
async def app_session() -> AsyncIterator[AsyncSession]:
    """A session on the restricted role, with NO tenant set (sees nothing)."""
    from app.db import SessionLocal

    async with SessionLocal() as session:
        yield session


def scoped_session_for(user_id):
    """Context manager yielding a restricted session scoped to `user_id`."""
    from app.db import SessionLocal, scope_to_user

    class _Ctx:
        async def __aenter__(self) -> AsyncSession:
            self._session = SessionLocal()
            scope_to_user(self._session, user_id)
            return self._session

        async def __aexit__(self, *exc) -> None:
            await self._session.close()

    return _Ctx()


@pytest.fixture
async def redis_client():
    from app.api.ratelimit import get_redis

    client = get_redis()
    await client.flushdb()
    yield client


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the ASGI app.

    base_url is https:// deliberately — httpx's cookie jar honours the Secure
    attribute and silently refuses to store or replay a Secure cookie over http.
    """
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        yield ac


@pytest.fixture
async def make_user(admin_session):
    """Create a user directly, bypassing the signup endpoint."""
    from app.models import User

    async def _make(email: str | None = None, **kwargs) -> User:
        user = User(
            email=email or f"user-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=kwargs.pop("password_hash", "not-a-real-hash"),
            **kwargs,
        )
        admin_session.add(user)
        await admin_session.commit()
        return user

    return _make


@pytest.fixture(scope="session")
async def live_server():
    """A real uvicorn server on the session event loop.

    WebSockets cannot be exercised through httpx's ASGI transport, and
    Starlette's sync TestClient runs the app in its own loop — which breaks
    immediately here, because the asyncpg pool and the redis client are bound
    to the loop that created them. Serving on the same loop as the tests keeps
    one loop throughout and exercises the real handshake, headers included.
    """
    import asyncio
    import socket

    import uvicorn

    from app.main import create_app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    config = uvicorn.Config(
        create_app(), host="127.0.0.1", port=port, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    else:  # pragma: no cover
        raise RuntimeError("uvicorn did not start")

    yield f"127.0.0.1:{port}"

    server.should_exit = True
    await task
