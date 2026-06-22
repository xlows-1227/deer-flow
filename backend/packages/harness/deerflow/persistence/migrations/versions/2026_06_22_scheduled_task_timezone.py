"""Store the IANA timezone for scheduled tasks.

Revision ID: 2026_06_22_task_timezone
Revises: 2026_06_08_external_api_v1
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_06_22_task_timezone"
down_revision = "2026_06_08_external_api_v1"
branch_labels = None
depends_on = None


def _column_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "scheduled_tasks" not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns("scheduled_tasks")}


def upgrade() -> None:
    columns = _column_names()
    if columns and "timezone" not in columns:
        op.add_column(
            "scheduled_tasks",
            sa.Column("timezone", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    if "timezone" in _column_names():
        op.drop_column("scheduled_tasks", "timezone")
