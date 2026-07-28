"""Add published-Agent usage and quota reservation ledgers.

Revision ID: 2026_07_14_agent_usage_quota
Revises: 2026_07_14_agent_conversation_scope
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_14_agent_usage_quota"
down_revision = "2026_07_14_agent_conversation_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "agent_quota_reservations" not in existing:
        op.create_table(
            "agent_quota_reservations",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("request_key", sa.String(128), nullable=False, unique=True),
            sa.Column("owner_user_id", sa.String(36), nullable=False),
            sa.Column("agent_id", sa.String(64), nullable=False),
            sa.Column("credential_id", sa.String(32), nullable=False),
            sa.Column("run_id", sa.String(64), unique=True),
            sa.Column("reserved_tokens", sa.Integer(), nullable=False),
            sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("terminal_status", sa.String(16)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("settled_at", sa.DateTime(timezone=True)),
        )
        for name, columns in (
            ("ix_agent_quota_reservations_owner_user_id", ["owner_user_id"]),
            ("ix_agent_quota_reservations_agent_id", ["agent_id"]),
            ("ix_agent_quota_reservations_credential_id", ["credential_id"]),
            ("ix_agent_quota_reservations_status", ["status"]),
            ("ix_agent_quota_reservations_expires_at", ["expires_at"]),
            ("ix_agent_quota_agent_status", ["agent_id", "status"]),
            ("ix_agent_quota_agent_created", ["agent_id", "created_at"]),
        ):
            op.create_index(name, "agent_quota_reservations", columns)

    if "agent_usage_records" not in existing:
        op.create_table(
            "agent_usage_records",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("owner_user_id", sa.String(36), nullable=False),
            sa.Column("agent_id", sa.String(64), nullable=False),
            sa.Column("source", sa.String(16), nullable=False),
            sa.Column("credential_id", sa.String(64), nullable=False),
            sa.Column("external_actor_hash", sa.String(64), nullable=False),
            sa.Column("conversation_id", sa.String(64), nullable=False),
            sa.Column("run_id", sa.String(64), nullable=False),
            sa.Column("model", sa.String(128), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("error_class", sa.String(128)),
            sa.Column("idempotency_key", sa.String(128)),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("run_id", name="uq_agent_usage_run_id"),
        )
        for name, columns in (
            ("ix_agent_usage_records_owner_user_id", ["owner_user_id"]),
            ("ix_agent_usage_records_agent_id", ["agent_id"]),
            ("ix_agent_usage_records_source", ["source"]),
            ("ix_agent_usage_records_credential_id", ["credential_id"]),
            ("ix_agent_usage_records_status", ["status"]),
            ("ix_agent_usage_records_correlation_id", ["correlation_id"]),
            ("ix_agent_usage_agent_created", ["agent_id", "created_at"]),
        ):
            op.create_index(name, "agent_usage_records", columns)


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "agent_usage_records" in existing:
        op.drop_table("agent_usage_records")
    if "agent_quota_reservations" in existing:
        op.drop_table("agent_quota_reservations")
