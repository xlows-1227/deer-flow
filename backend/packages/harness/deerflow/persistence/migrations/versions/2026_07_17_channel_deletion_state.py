"""Add durable Published-Agent channel deletion state.

Revision ID: 2026_07_17_channel_deletion_state
Revises: 2026_07_14_channel_mappings
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_17_channel_deletion_state"
down_revision = "2026_07_14_channel_mappings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_channels")}
    if "delete_previous_status" not in columns:
        op.add_column("agent_channels", sa.Column("delete_previous_status", sa.String(16)))
    if "runtime_lease_token" not in columns:
        op.add_column("agent_channels", sa.Column("runtime_lease_token", sa.String(64)))
    if "runtime_lease_expires_at" not in columns:
        op.add_column("agent_channels", sa.Column("runtime_lease_expires_at", sa.DateTime(timezone=True)))
    if "runtime_generation" not in columns:
        op.add_column("agent_channels", sa.Column("runtime_generation", sa.Integer(), nullable=False, server_default=sa.text("0")))
    if "runtime_stop_requested" not in columns:
        op.add_column("agent_channels", sa.Column("runtime_stop_requested", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "health_revision" not in columns:
        op.add_column("agent_channels", sa.Column("health_revision", sa.Integer(), nullable=False, server_default=sa.text("0")))
    if "secret_cleanup_ref" not in columns:
        op.add_column("agent_channels", sa.Column("secret_cleanup_ref", sa.String(128)))
    if "secret_cleanup_reason" not in columns:
        op.add_column("agent_channels", sa.Column("secret_cleanup_reason", sa.String(32)))
    if "secret_cleanup_not_before" not in columns:
        op.add_column("agent_channels", sa.Column("secret_cleanup_not_before", sa.DateTime(timezone=True)))
    if "rotation_previous_secret_ref" not in columns:
        op.add_column("agent_channels", sa.Column("rotation_previous_secret_ref", sa.String(128)))
    if "agent_channel_secret_ingests" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "agent_channel_secret_ingests",
            sa.Column("secret_ref", sa.String(128), primary_key=True),
            sa.Column("agent_id", sa.String(64), nullable=False),
            sa.Column("binding_id", sa.String(64), nullable=False),
            sa.Column("owner_user_id", sa.String(128), nullable=False),
            sa.Column("state", sa.String(16), nullable=False, server_default="reserved"),
            sa.Column("writer_token", sa.String(64)),
            sa.Column("writer_generation", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("writer_lease_expires_at", sa.DateTime(timezone=True)),
            sa.Column("claim_token", sa.String(64)),
            sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
            sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_agent_channel_secret_ingests_agent_id", "agent_channel_secret_ingests", ["agent_id"])
        op.create_index("ix_agent_channel_secret_ingests_binding_id", "agent_channel_secret_ingests", ["binding_id"])
        op.create_index(
            "ix_agent_channel_secret_ingests_due",
            "agent_channel_secret_ingests",
            ["state", "not_before", "writer_lease_expires_at", "claim_expires_at"],
        )
    else:
        ingest_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_channel_secret_ingests")}
        if "writer_token" not in ingest_columns:
            op.add_column("agent_channel_secret_ingests", sa.Column("writer_token", sa.String(64)))
        if "writer_generation" not in ingest_columns:
            op.add_column(
                "agent_channel_secret_ingests",
                sa.Column("writer_generation", sa.Integer(), nullable=False, server_default=sa.text("0")),
            )
        if "writer_lease_expires_at" not in ingest_columns:
            op.add_column(
                "agent_channel_secret_ingests",
                sa.Column("writer_lease_expires_at", sa.DateTime(timezone=True)),
            )


def downgrade() -> None:
    if "agent_channel_secret_ingests" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("agent_channel_secret_ingests")
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_channels")}
    for column_name in (
        "rotation_previous_secret_ref",
        "secret_cleanup_not_before",
        "secret_cleanup_reason",
        "secret_cleanup_ref",
        "runtime_generation",
        "health_revision",
        "runtime_stop_requested",
        "runtime_lease_expires_at",
        "runtime_lease_token",
    ):
        if column_name in columns:
            op.drop_column("agent_channels", column_name)
    if "delete_previous_status" in columns:
        op.drop_column("agent_channels", "delete_previous_status")
