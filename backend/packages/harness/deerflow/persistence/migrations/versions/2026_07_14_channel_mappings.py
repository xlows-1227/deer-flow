"""Add persistent channel conversation mappings and event deduplication.

Revision ID: 2026_07_14_channel_mappings
Revises: 2026_07_14_agent_channels
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_14_channel_mappings"
down_revision = "2026_07_14_agent_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "channel_conversation_mappings" not in tables:
        op.create_table(
            "channel_conversation_mappings",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("binding_id", sa.String(64), nullable=False),
            sa.Column("agent_id", sa.String(64), nullable=False),
            sa.Column("chat_id", sa.String(256), nullable=False),
            sa.Column("actor_scope", sa.String(256), nullable=False),
            sa.Column("topic_id", sa.String(256), nullable=False, server_default=""),
            sa.Column("thread_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "binding_id",
                "chat_id",
                "actor_scope",
                "topic_id",
                name="uq_channel_mapping_scope",
            ),
            sa.UniqueConstraint("thread_id", name="uq_channel_conversation_mappings_thread_id"),
        )
        op.create_index(
            "ix_channel_mappings_binding_agent",
            "channel_conversation_mappings",
            ["binding_id", "agent_id"],
        )

    if "channel_event_dedup" not in tables:
        op.create_table(
            "channel_event_dedup",
            sa.Column("binding_id", sa.String(64), primary_key=True),
            sa.Column("event_id", sa.String(128), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_channel_event_dedup_created_at", "channel_event_dedup", ["created_at"])


def downgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "channel_event_dedup" in tables:
        op.drop_table("channel_event_dedup")
    if "channel_conversation_mappings" in tables:
        op.drop_table("channel_conversation_mappings")
