"""Declarative base, naming conventions, and shared column types.

`Base` lives here rather than in `app/db.py` because `db.py` constructs engines;
alembic's `env.py` needs the metadata without pulling a live engine into scope.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit names for every constraint, so autogenerate produces stable diffs and
# downgrade() can drop things by name.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


# gen_random_uuid() is built into PG13+; no pgcrypto/uuid-ossp needed. Both a
# Python-side default and a server_default are set so raw-SQL inserts (migrations,
# psql, tests) get an id too.
uuid_pk = Annotated[
    uuid.UUID,
    mapped_column(
        sa.Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    ),
]

# All timestamps are timezone-aware; the wire format is ISO-8601 UTC.
ts = Annotated[datetime, mapped_column(sa.DateTime(timezone=True))]
ts_now = Annotated[
    datetime,
    mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
]

# 32 raw bytes of sha256. Exact-width and half the size of hex; read it in psql
# with encode(token_hash, 'hex').
sha256_hash = Annotated[bytes, mapped_column(sa.LargeBinary(32), nullable=False)]


class TimestampMixin:
    created_at: Mapped[ts_now]
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
