"""Scope published-agent conversations by Agent credential.

Revision ID: 2026_07_14_agent_conversation_scope
Revises: 2026_07_14_agent_api_keys
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_14_agent_conversation_scope"
down_revision = "2026_07_14_agent_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "external_conversations" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("external_conversations")}
    if "credential_id" not in columns:
        with op.batch_alter_table("external_conversations") as batch:
            batch.add_column(sa.Column("credential_id", sa.String(32)))
            batch.create_index(
                "ix_external_conversations_credential_id",
                ["credential_id"],
            )
            batch.create_index(
                "ix_external_conversations_agent_credential",
                ["agent_id", "credential_id"],
            )
            batch.create_unique_constraint(
                "uq_agent_credential_conversation_mapping",
                ["agent_id", "credential_id", "source", "external_conversation_id"],
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "external_conversations" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("external_conversations")}
    if "credential_id" in columns:
        with op.batch_alter_table("external_conversations") as batch:
            batch.drop_constraint(
                "uq_agent_credential_conversation_mapping",
                type_="unique",
            )
            batch.drop_index("ix_external_conversations_agent_credential")
            batch.drop_index("ix_external_conversations_credential_id")
            batch.drop_column("credential_id")
