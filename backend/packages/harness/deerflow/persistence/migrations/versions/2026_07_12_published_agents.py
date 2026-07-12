"""Add published_agents, agent_drafts and their sub-tables.

Revision ID: 2026_07_12_published_agents
Revises: 2026_07_09_umodel_caps
Create Date: 2026-07-12

Tables (design doc §7.1 / §7.2, dev plan F1.1):
- published_agents          stable agent identity (owner + slug unique)
- agent_drafts              1:1 mutable draft with optimistic revision
- agent_draft_skills        (agent_id, skill_name) skill selection
- agent_draft_connector_grants   (agent_id, connector_instance_id, capability)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_12_published_agents"
down_revision = "2026_07_09_umodel_caps"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _table_names()

    if "published_agents" not in existing:
        op.create_table(
            "published_agents",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("owner_user_id", sa.String(36), nullable=False),
            sa.Column("slug", sa.String(64), nullable=False),
            sa.Column("display_name", sa.String(128), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("avatar_ref", sa.String(256)),
            sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
            sa.Column("current_release_id", sa.String(64)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("owner_user_id", "slug", name="uq_published_agents_owner_slug"),
        )
        op.create_index("ix_published_agents_owner_user_id", "published_agents", ["owner_user_id"])
        op.create_index("ix_published_agents_status", "published_agents", ["status"])

    if "agent_drafts" not in existing:
        op.create_table(
            "agent_drafts",
            sa.Column("agent_id", sa.String(64), primary_key=True),
            sa.Column("agent_markdown", sa.Text(), nullable=False, server_default=""),
            sa.Column("soul_markdown", sa.Text(), nullable=False, server_default=""),
            sa.Column("model_name", sa.String(128)),
            sa.Column("tool_groups_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("quota_overrides_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_by", sa.String(36), nullable=False),
        )

    if "agent_draft_skills" not in existing:
        op.create_table(
            "agent_draft_skills",
            sa.Column("agent_id", sa.String(64), primary_key=True),
            sa.Column("skill_name", sa.String(128), primary_key=True),
            sa.Column("source", sa.String(16), nullable=False, server_default="public"),
        )
        op.create_index("ix_agent_draft_skills_agent", "agent_draft_skills", ["agent_id"])

    if "agent_draft_connector_grants" not in existing:
        op.create_table(
            "agent_draft_connector_grants",
            sa.Column("agent_id", sa.String(64), primary_key=True),
            sa.Column("connector_instance_id", sa.String(64), primary_key=True),
            sa.Column("capability", sa.String(80), primary_key=True),
        )
        op.create_index(
            "ix_agent_draft_connector_grants_agent",
            "agent_draft_connector_grants",
            ["agent_id"],
        )


def downgrade() -> None:
    existing = _table_names()
    if "agent_draft_connector_grants" in existing:
        op.drop_index("ix_agent_draft_connector_grants_agent", table_name="agent_draft_connector_grants")
        op.drop_table("agent_draft_connector_grants")
    if "agent_draft_skills" in existing:
        op.drop_index("ix_agent_draft_skills_agent", table_name="agent_draft_skills")
        op.drop_table("agent_draft_skills")
    if "agent_drafts" in existing:
        op.drop_table("agent_drafts")
    if "published_agents" in existing:
        op.drop_index("ix_published_agents_status", table_name="published_agents")
        op.drop_index("ix_published_agents_owner_user_id", table_name="published_agents")
        op.drop_table("published_agents")
