"""Add thinking capability flags to user_models.

Revision ID: 2026_07_09_umodel_caps
Revises: 2026_07_01_invite_codes
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_09_umodel_caps"
down_revision = "2026_07_01_invite_codes"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    if "user_models" not in sa.inspect(op.get_bind()).get_table_names():
        return

    columns = _column_names("user_models")
    if "supports_thinking" not in columns:
        op.add_column(
            "user_models",
            sa.Column("supports_thinking", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if "supports_reasoning_effort" not in columns:
        op.add_column(
            "user_models",
            sa.Column("supports_reasoning_effort", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    if "user_models" not in sa.inspect(op.get_bind()).get_table_names():
        return

    columns = _column_names("user_models")
    if "supports_reasoning_effort" in columns:
        op.drop_column("user_models", "supports_reasoning_effort")
    if "supports_thinking" in columns:
        op.drop_column("user_models", "supports_thinking")
