"""Record how much real history each forecast was fitted on.

Nullable: rows written before this column existed have no honest value to
backfill, and a wrong number here would be rendered as a confidence label the
user could act on. Null reads as "unknown", which ForecastOut.confidence maps
to its most cautious answer.

Revision ID: 0012_forecast_history
Revises: 0011_fcm_tokens
Create Date: 2026-08-23 15:21:40.764103+00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_forecast_history"
down_revision: str | Sequence[str] | None = "0011_fcm_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "metric_forecasts", sa.Column("history_seconds", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("metric_forecasts", "history_seconds")
