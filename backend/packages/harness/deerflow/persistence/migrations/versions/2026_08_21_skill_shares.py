"""Create skill_shares table for custom-skill user-to-user grants.

Implements §权限矩阵 of the sharing-permissions design v2.  This migration
only adds the bare catalog table; the share policy enforcement lives in
:mod:`deerflow.persistence.skill_share.store` and the Gateway routers.

Revision ID: 2026_08_21_skill_shares
Revises: 2026_07_30_widen_cred_ref
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_08_21_skill_shares"
down_revision = "2026_07_30_widen_cred_ref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "skill_shares" in inspector.get_table_names():
        return

    op.create_table(
        "skill_shares",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("skill_name", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("shared_with_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_skill_shares"),
        sa.UniqueConstraint(
            "skill_name",
            "owner_user_id",
            "shared_with_user_id",
            name="uq_skill_shares_grant",
        ),
    )
    # Three separate indices cover the common access patterns:
    #   * list sharees for (skill, owner) → skill_name + owner_user_id
    #   * list grants a user has received → shared_with_user_id
    #   * owner-wide audits → owner_user_id
    op.create_index("ix_skill_shares_lookup_grant", "skill_shares", ["skill_name", "owner_user_id"])
    op.create_index("ix_skill_shares_shared_with", "skill_shares", ["shared_with_user_id"])
    op.create_index("ix_skill_shares_owner", "skill_shares", ["owner_user_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "skill_shares" not in inspector.get_table_names():
        return
    with op.batch_alter_table("skill_shares") as batch_op:
        batch_op.drop_index("ix_skill_shares_lookup_grant")
        batch_op.drop_index("ix_skill_shares_shared_with")
        batch_op.drop_index("ix_skill_shares_owner")
    op.drop_table("skill_shares")
