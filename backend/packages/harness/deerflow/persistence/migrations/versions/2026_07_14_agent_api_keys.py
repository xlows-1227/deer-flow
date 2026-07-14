"""Add independently managed Agent-scoped API Keys.

Revision ID: 2026_07_14_agent_api_keys
Revises: 2026_07_13_draft_skill_mode
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_14_agent_api_keys"
down_revision = "2026_07_13_draft_skill_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "agent_api_keys" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "agent_api_keys",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("secret_hash", sa.String(256), nullable=False),
        sa.Column("key_prefix", sa.String(64), nullable=False),
        sa.Column("last_four", sa.String(4), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("quota_overrides_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("rotation_of", sa.String(32)),
    )
    op.create_index("ix_agent_api_keys_agent_id", "agent_api_keys", ["agent_id"])
    op.create_index("ix_agent_api_keys_key_prefix", "agent_api_keys", ["key_prefix"])
    op.create_index("ix_agent_api_keys_status", "agent_api_keys", ["status"])
    op.create_index("ix_agent_api_keys_rotation_of", "agent_api_keys", ["rotation_of"])
    op.create_index("ix_agent_api_keys_agent_status", "agent_api_keys", ["agent_id", "status"])


def downgrade() -> None:
    if "agent_api_keys" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("agent_api_keys")

