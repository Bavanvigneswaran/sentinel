"""Alembic environment.

Migrations run as the OWNER role (`admin_database_url`), not the restricted app
role: they create tables, grants, and RLS policies, none of which the app role
may do.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  — ensures every table is registered on Base.metadata
from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# TimescaleDB creates chunks and catalog tables in its own schemas. Without this
# filter the first autogenerate after Phase 2 adds a hypertable would emit a
# DROP TABLE for every chunk.
SKIP_SCHEMAS = {
    "_timescaledb_internal",
    "_timescaledb_catalog",
    "_timescaledb_config",
    "_timescaledb_cache",
    "_timescaledb_functions",
    "timescaledb_information",
    "timescaledb_experimental",
}
SKIP_TABLES = {"alembic_version"}


def include_object(obj, name, type_, reflected, compare_to) -> bool:  # noqa: ANN001
    if getattr(obj, "schema", None) in SKIP_SCHEMAS:
        return False
    if type_ == "table" and name in SKIP_TABLES:
        return False
    return True


def _db_url() -> str:
    # Deliberately not config.set_main_option(): ConfigParser applies
    # %-interpolation, so a URL-encoded password containing '%' would blow up
    # with a confusing InterpolationSyntaxError.
    return get_settings().admin_database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(_db_url(), poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
