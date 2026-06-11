"""新增 External API V1 持久化表。

Revision ID: 2026_06_08_external_api_v1
Revises: 2026_06_04_connector_username
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_06_08_external_api_v1"
down_revision = "2026_06_04_connector_username"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _table_names()
    if "api_keys" not in existing:
        op.create_table(
            "api_keys",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("secret_hash", sa.String(64), nullable=False),
            sa.Column("key_prefix", sa.String(64), nullable=False),
            sa.Column("last_four", sa.String(4), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("scopes_json", sa.JSON(), nullable=False),
            sa.Column("allowed_skills_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True)),
            sa.Column("expires_at", sa.DateTime(timezone=True)),
            sa.Column("revoked_at", sa.DateTime(timezone=True)),
            sa.Column("revoked_reason", sa.String(128)),
        )
        op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
        op.create_index("ix_api_keys_status", "api_keys", ["status"])
        op.create_index("ix_api_keys_user_status", "api_keys", ["user_id", "status"])
        op.create_index(
            "uq_api_keys_user_active",
            "api_keys",
            ["user_id"],
            unique=True,
            sqlite_where=sa.text("status = 'active'"),
            postgresql_where=sa.text("status = 'active'"),
        )

    if "external_conversations" not in existing:
        op.create_table(
            "external_conversations",
            sa.Column("conversation_id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("source", sa.String(64), nullable=False),
            sa.Column("external_conversation_id", sa.String(256)),
            sa.Column("thread_id", sa.String(64), nullable=False, unique=True),
            sa.Column("agent_id", sa.String(128), nullable=False),
            sa.Column("default_skill_name", sa.String(128)),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("title", sa.String(256)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("closed_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("user_id", "source", "external_conversation_id", name="uq_external_conversation_mapping"),
        )
        op.create_index("ix_external_conversations_user_id", "external_conversations", ["user_id"])
        op.create_index("ix_external_conversations_status", "external_conversations", ["status"])
        op.create_index(
            "ix_external_conversations_mapping",
            "external_conversations",
            ["user_id", "source", "external_conversation_id"],
        )

    if "external_idempotency_keys" not in existing:
        op.create_table(
            "external_idempotency_keys",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("api_key_id", sa.String(32), nullable=False),
            sa.Column("idempotency_key", sa.String(128), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("run_id", sa.String(64)),
            sa.Column("response_status", sa.Integer()),
            sa.Column("response_json", sa.JSON()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("api_key_id", "idempotency_key", name="uq_external_idempotency_key"),
        )
        op.create_index("ix_external_idempotency_keys_user_id", "external_idempotency_keys", ["user_id"])
        op.create_index("ix_external_idempotency_keys_api_key_id", "external_idempotency_keys", ["api_key_id"])
        op.create_index("ix_external_idempotency_expires", "external_idempotency_keys", ["expires_at"])

    if "external_api_audit_logs" not in existing:
        op.create_table(
            "external_api_audit_logs",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("request_id", sa.String(64), nullable=False),
            sa.Column("user_id", sa.String(36)),
            sa.Column("api_key_id", sa.String(32)),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("resource_type", sa.String(32)),
            sa.Column("resource_id", sa.String(64)),
            sa.Column("skill_name", sa.String(128)),
            sa.Column("method", sa.String(8), nullable=False),
            sa.Column("path_template", sa.String(256), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("client_ip_hash", sa.String(64)),
            sa.Column("user_agent", sa.String(256)),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_external_api_audit_logs_request_id", "external_api_audit_logs", ["request_id"])
        op.create_index("ix_external_api_audit_logs_user_id", "external_api_audit_logs", ["user_id"])
        op.create_index("ix_external_api_audit_logs_api_key_id", "external_api_audit_logs", ["api_key_id"])
        op.create_index("ix_external_audit_user_created", "external_api_audit_logs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("external_api_audit_logs")
    op.drop_table("external_idempotency_keys")
    op.drop_table("external_conversations")
    op.drop_table("api_keys")
