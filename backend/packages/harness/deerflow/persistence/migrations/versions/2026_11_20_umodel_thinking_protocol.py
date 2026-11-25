"""Add thinking_protocol to user_models.

User-defined models could not express *how* to toggle provider-side thinking,
so `when_thinking_enabled` was never present on their `ModelConfig` and pro
mode silently sent no thinking parameters (or sent `reasoning_effort` to a
gateway whose default is `enable_thinking=false`, which it rejects with 400).

`thinking_protocol` selects the wire format:
  * "none" (default) — never send thinking parameters, provider defaults apply
  * "qwen"           — extra_body.chat_template_kwargs.enable_thinking

Revision ID: 2026_11_20_umodel_thinking_protocol
Revises: 2026_09_17_user_soft_delete
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_11_20_umodel_thinking_protocol"
down_revision = "2026_09_17_user_soft_delete"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    if "user_models" not in sa.inspect(op.get_bind()).get_table_names():
        return

    if "thinking_protocol" not in _column_names("user_models"):
        op.add_column(
            "user_models",
            sa.Column("thinking_protocol", sa.String(length=32), nullable=False, server_default="none"),
        )


def downgrade() -> None:
    if "user_models" not in sa.inspect(op.get_bind()).get_table_names():
        return

    if "thinking_protocol" in _column_names("user_models"):
        op.drop_column("user_models", "thinking_protocol")
