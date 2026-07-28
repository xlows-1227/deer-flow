"""Record whether an agent draft inherits all skills or uses an explicit set.

Revision ID: 2026_07_13_draft_skill_mode
Revises: 2026_07_12_widen_agent_ids
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_13_draft_skill_mode"
down_revision = "2026_07_12_widen_agent_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "agent_drafts" not in inspector.get_table_names():
        return
    if "skill_selection_mode" in {column["name"] for column in inspector.get_columns("agent_drafts")}:
        return
    # Historical rows without skill children are ambiguous: they may mean
    # "unset" or an intentional empty set. Backfill all of them to the
    # conservative explicit mode; only new conversational/legacy imports that
    # deliberately omit skills opt into inheritance.
    op.add_column(
        "agent_drafts",
        sa.Column(
            "skill_selection_mode",
            sa.String(length=16),
            nullable=False,
            server_default="explicit",
        ),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "agent_drafts" not in inspector.get_table_names():
        return
    if "skill_selection_mode" not in {column["name"] for column in inspector.get_columns("agent_drafts")}:
        return
    with op.batch_alter_table("agent_drafts") as batch_op:
        batch_op.drop_column("skill_selection_mode")
