"""Add per-user custom LLM model configuration table.

Revision ID: 2026_06_26_user_models
Revises: 2026_06_22_task_timezone
Create Date: 2026-06-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_06_26_user_models"
down_revision = "2026_06_22_task_timezone"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _table_names()
    if "user_models" in existing:
        return
    op.create_table(
        "user_models",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(160)),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("base_url", sa.String(512)),
        sa.Column("api_key_ref", sa.String(512)),
        sa.Column("api_key_last_four", sa.String(4)),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_user_models_user_id", "user_models", ["user_id"])
    op.create_index("ix_user_models_user_enabled", "user_models", ["user_id", "enabled"])


def downgrade() -> None:
    if "user_models" not in _table_names():
        return
    op.drop_index("ix_user_models_user_enabled", table_name="user_models")
    op.drop_index("ix_user_models_user_id", table_name="user_models")
    op.drop_table("user_models")
