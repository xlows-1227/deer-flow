"""Add release-aware usage and quota rejection operations metrics.

Revision ID: 2026_07_26_agent_ops_metrics
Revises: 2026_07_17_channel_deletion_state
Create Date: 2026-07-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_26_agent_ops_metrics"
down_revision = "2026_07_17_channel_deletion_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "agent_usage_records" in existing:
        usage_columns = {column["name"] for column in inspector.get_columns("agent_usage_records")}
        if "release_id" not in usage_columns or "event_latency_ms" not in usage_columns:
            with op.batch_alter_table("agent_usage_records") as batch:
                if "release_id" not in usage_columns:
                    batch.add_column(sa.Column("release_id", sa.String(64)))
                    batch.create_index(
                        "ix_agent_usage_records_release_id",
                        ["release_id"],
                    )
                if "event_latency_ms" not in usage_columns:
                    batch.add_column(sa.Column("event_latency_ms", sa.Integer()))

    if "agent_quota_rejections" not in existing:
        op.create_table(
            "agent_quota_rejections",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("owner_user_id", sa.String(36), nullable=False),
            sa.Column("agent_id", sa.String(64), nullable=False),
            sa.Column("credential_id", sa.String(64), nullable=False),
            sa.Column("source", sa.String(16), nullable=False),
            sa.Column("reason", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for name, columns in (
            ("ix_agent_quota_rejections_owner_user_id", ["owner_user_id"]),
            ("ix_agent_quota_rejections_agent_id", ["agent_id"]),
            ("ix_agent_quota_rejections_credential_id", ["credential_id"]),
            ("ix_agent_quota_rejections_source", ["source"]),
            ("ix_agent_quota_rejections_reason", ["reason"]),
            (
                "ix_agent_quota_rejection_agent_created",
                ["agent_id", "created_at"],
            ),
        ):
            op.create_index(name, "agent_quota_rejections", columns)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    if "agent_quota_rejections" in existing:
        op.drop_table("agent_quota_rejections")
    if "agent_usage_records" in existing:
        usage_columns = {column["name"] for column in sa.inspect(bind).get_columns("agent_usage_records")}
        if "release_id" in usage_columns or "event_latency_ms" in usage_columns:
            with op.batch_alter_table("agent_usage_records") as batch:
                if "release_id" in usage_columns:
                    batch.drop_index("ix_agent_usage_records_release_id")
                    batch.drop_column("release_id")
                if "event_latency_ms" in usage_columns:
                    batch.drop_column("event_latency_ms")
