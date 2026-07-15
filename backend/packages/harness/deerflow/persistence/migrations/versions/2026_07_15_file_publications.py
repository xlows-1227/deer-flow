"""Add permanent public HTML file publications.

Revision ID: 2026_07_15_file_publications
Revises: 2026_07_13_file_shares
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_15_file_publications"
down_revision = "2026_07_13_file_shares"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "file_publications" in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        "file_publications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("public_token", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("source_path", sa.String(length=2048), nullable=False),
        sa.Column("source_identity", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_token", name="uq_file_publications_public_token"),
        sa.UniqueConstraint(
            "owner_user_id",
            "thread_id",
            "source_path",
            name="uq_file_publications_owner_source",
        ),
    )
    op.create_index("ix_file_publications_owner_user_id", "file_publications", ["owner_user_id"])


def downgrade() -> None:
    if "file_publications" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("ix_file_publications_owner_user_id", table_name="file_publications")
    op.drop_table("file_publications")
