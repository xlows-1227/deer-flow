"""Add stable Agent channel bindings.

Revision ID: 2026_07_14_agent_channels
Revises: 2026_07_14_agent_audit_principals
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_14_agent_channels"
down_revision = "2026_07_14_agent_audit_principals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "agent_channels" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "agent_channels",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("channel_type", sa.String(16), nullable=False, server_default="feishu"),
        sa.Column("app_id", sa.String(128), nullable=False),
        sa.Column("secret_ref", sa.String(128), nullable=False),
        sa.Column("connection_mode", sa.String(16), nullable=False, server_default="websocket"),
        sa.Column("status", sa.String(16), nullable=False, server_default="inactive"),
        sa.Column("health", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("health_detail", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_agent_channels_agent_id", "agent_channels", ["agent_id"])
    op.create_index("ix_agent_channels_status", "agent_channels", ["status"])
    op.create_index("ix_agent_channels_agent_status", "agent_channels", ["agent_id", "status"])
    op.create_index(
        "uq_agent_channels_active",
        "agent_channels",
        ["agent_id", "channel_type"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    if "agent_channels" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("agent_channels")
