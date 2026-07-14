"""Add read-only file shares between registered users.

Revision ID: 2026_07_13_file_shares
Revises: 2026_07_09_umodel_caps
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_13_file_shares"
down_revision = "2026_07_09_umodel_caps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "file_shares" in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        "file_shares",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_user_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_path", sa.String(length=2048), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "recipient_user_id",
            "source_type",
            "source_path",
            "thread_id",
            name="uq_file_shares_recipient_source",
        ),
    )
    op.create_index("ix_file_shares_owner_user_id", "file_shares", ["owner_user_id"])
    op.create_index("ix_file_shares_recipient_user_id", "file_shares", ["recipient_user_id"])


def downgrade() -> None:
    if "file_shares" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("ix_file_shares_recipient_user_id", table_name="file_shares")
    op.drop_index("ix_file_shares_owner_user_id", table_name="file_shares")
    op.drop_table("file_shares")
