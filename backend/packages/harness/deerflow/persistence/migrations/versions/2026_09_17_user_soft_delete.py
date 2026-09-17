"""Add soft-delete columns to the users table.

Admin user management ("Settings → User Management") deletes accounts
logically: the row survives so skill-share grants and thread ownership
stay resolvable, but ``deleted`` accounts can no longer authenticate and
are filtered out of user listings and share pickers.

Revision ID: 2026_09_17_user_soft_delete
Revises: 2026_08_21_skill_shares
Create Date: 2026-09-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_09_17_user_soft_delete"
down_revision = "2026_08_21_skill_shares"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "deleted" not in columns:
        op.add_column("users", sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "deleted_at" not in columns:
        op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "deleted_at" in columns:
        op.drop_column("users", "deleted_at")
    if "deleted" in columns:
        op.drop_column("users", "deleted")
