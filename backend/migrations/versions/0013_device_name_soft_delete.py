"""Device names are unique among *live* devices, not among all rows ever.

`uq_devices_user_id_name` covered soft-deleted rows too, so removing a device
and adding it back under the same name was refused — "You already have a
device with that name", against a device list that no longer contained one.
The delete is soft precisely so Phase 2's metric rows keep a valid foreign
key; that is a storage decision and it had been leaking out as a user-facing
rule about names.

A partial unique index is the whole fix: the same guarantee for every device a
user can actually see, and no opinion at all about the ones they removed.
Several removed devices may therefore share a name, which is correct — they
are history, and history repeats.

Revision ID: 0013_device_name_soft_delete
Revises: 0012_forecast_history
Create Date: 2026-08-24 19:44:00.000000+00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_device_name_soft_delete"
down_revision: str | Sequence[str] | None = "0012_forecast_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "uq_devices_user_id_name"
_INDEX = "uq_devices_user_id_name_live"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "devices", type_="unique")
    op.create_index(
        _INDEX,
        "devices",
        ["user_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    # Only reversible while no two live-or-removed devices share a name. A
    # deployment that used the freedom this migration grants cannot go back
    # without choosing which of the colliding rows to rename, which is not a
    # decision a downgrade may make on someone's behalf.
    op.drop_index(_INDEX, table_name="devices")
    op.create_unique_constraint(_CONSTRAINT, "devices", ["user_id", "name"])
