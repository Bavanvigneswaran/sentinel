"""Create the database named by ADMIN_DATABASE_URL, if it is not already there.

    python scripts/create_database.py
    python scripts/create_database.py --drop-existing

Alembic can migrate a database but cannot create one, and `docker compose up`
creates only the single database named in docker-compose.yml. So every
environment that is not the default dev one — the CI database, most obviously —
needs this one step in between, and it is the step that is easy to leave out of
a workflow and then spend a while diagnosing: the failure is asyncpg's
`InvalidCatalogNameError` raised from inside `alembic upgrade`, which reads as a
migration problem.

Refuses to touch a database whose name does not end in `_ci` or `_test` when
--drop-existing is passed. Creating is harmless; dropping is not, and the two
share a command line.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

DISPOSABLE_SUFFIXES = ("_ci", "_test")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="drop it first (refused unless the name ends in _ci or _test)",
    )
    args = parser.parse_args()

    url = sa.make_url(get_settings().admin_database_url)
    target = url.database
    if not target:
        raise SystemExit("ADMIN_DATABASE_URL names no database")

    if args.drop_existing and not target.endswith(DISPOSABLE_SUFFIXES):
        raise SystemExit(
            f"refusing to drop {target!r}: --drop-existing is only for databases "
            f"whose name ends in {' or '.join(DISPOSABLE_SUFFIXES)}"
        )

    # CREATE/DROP DATABASE cannot run inside a transaction.
    engine = create_async_engine(
        url.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with engine.connect() as conn:
            if args.drop_existing:
                await conn.execute(
                    sa.text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :db AND pid <> pg_backend_pid()"
                    ),
                    {"db": target},
                )
                await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{target}"'))
                print(f"dropped {target}")

            exists = await conn.scalar(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :db"), {"db": target}
            )
            if exists:
                print(f"{target} already exists")
            else:
                # The name comes from our own settings, not from a request, and
                # an identifier cannot be a bind parameter.
                await conn.execute(sa.text(f'CREATE DATABASE "{target}"'))  # noqa: S608
                print(f"created {target}")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
