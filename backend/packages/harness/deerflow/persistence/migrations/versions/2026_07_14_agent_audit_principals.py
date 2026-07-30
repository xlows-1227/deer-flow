"""Add dual principals to external API audit events.

Revision ID: 2026_07_14_agent_audit_principals
Revises: 2026_07_14_agent_usage_quota
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_14_agent_audit_principals"
down_revision = "2026_07_14_agent_usage_quota"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "external_api_audit_logs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("external_api_audit_logs")}
    indexes = {index["name"] for index in inspector.get_indexes("external_api_audit_logs")}
    additions = (
        ("owner_user_id", sa.String(36), True),
        ("agent_id", sa.String(64), True),
        ("credential_id", sa.String(64), True),
        ("external_actor_hash", sa.String(64), False),
        ("source", sa.String(16), True),
    )
    with op.batch_alter_table("external_api_audit_logs") as batch:
        for name, column_type, indexed in additions:
            if name not in columns:
                batch.add_column(sa.Column(name, column_type))
            index_name = f"ix_external_api_audit_logs_{name}"
            if indexed and index_name not in indexes:
                batch.create_index(index_name, [name])
        if "ix_external_audit_agent_created" not in indexes:
            batch.create_index(
                "ix_external_audit_agent_created",
                ["agent_id", "created_at"],
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "external_api_audit_logs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("external_api_audit_logs")}
    with op.batch_alter_table("external_api_audit_logs") as batch:
        batch.drop_index("ix_external_audit_agent_created")
        for name in (
            "source",
            "external_actor_hash",
            "credential_id",
            "agent_id",
            "owner_user_id",
        ):
            if name in columns:
                if name != "external_actor_hash":
                    batch.drop_index(f"ix_external_api_audit_logs_{name}")
                batch.drop_column(name)
