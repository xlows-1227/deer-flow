"""Add per-user MCP and image generation configuration tables.

Revision ID: 2026_06_30_user_extensions
Revises: 2026_06_26_user_models
Create Date: 2026-06-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_06_30_user_extensions"
down_revision = "2026_06_26_user_models"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _table_names()
    if "user_mcp_servers" not in existing:
        op.create_table(
            "user_mcp_servers",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("type", sa.String(32), nullable=False, server_default="stdio"),
            sa.Column("command", sa.String(512)),
            sa.Column("args", sa.JSON()),
            sa.Column("url", sa.String(1024)),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("secrets_ref", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("user_id", "name", name="uq_user_mcp_servers_user_name"),
        )
        op.create_index("ix_user_mcp_servers_user_id", "user_mcp_servers", ["user_id"])

    if "user_mcp_server_states" not in existing:
        op.create_table(
            "user_mcp_server_states",
            sa.Column("user_id", sa.String(64), primary_key=True),
            sa.Column("server_name", sa.String(128), primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "user_image_settings" not in existing:
        op.create_table(
            "user_image_settings",
            sa.Column("user_id", sa.String(64), primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("default_provider", sa.String(64)),
            sa.Column("output_subdir", sa.String(256), nullable=False, server_default="generated-images"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "user_image_providers" not in existing:
        op.create_table(
            "user_image_providers",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("api_key_ref", sa.String(512)),
            sa.Column("api_key_last_four", sa.String(4)),
            sa.Column("base_url", sa.String(512)),
            sa.Column("model", sa.String(128)),
            sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default="120"),
            sa.Column("trust_env", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("params", sa.JSON()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("user_id", "provider", name="uq_user_image_providers_user_provider"),
        )
        op.create_index("ix_user_image_providers_user_id", "user_image_providers", ["user_id"])
        op.create_index("ix_user_image_providers_user_enabled", "user_image_providers", ["user_id", "enabled"])


def downgrade() -> None:
    existing = _table_names()
    if "user_image_providers" in existing:
        op.drop_index("ix_user_image_providers_user_enabled", table_name="user_image_providers")
        op.drop_index("ix_user_image_providers_user_id", table_name="user_image_providers")
        op.drop_table("user_image_providers")
    if "user_image_settings" in existing:
        op.drop_table("user_image_settings")
    if "user_mcp_server_states" in existing:
        op.drop_table("user_mcp_server_states")
    if "user_mcp_servers" in existing:
        op.drop_index("ix_user_mcp_servers_user_id", table_name="user_mcp_servers")
        op.drop_table("user_mcp_servers")
