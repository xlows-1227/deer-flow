"""Add agent_releases, skill_revisions and the release sub-tables.

Revision ID: 2026_07_12_agent_releases
Revises: 2026_07_12_published_agents
Create Date: 2026-07-12

Tables (design doc §7.3–§7.5, dev plan F1.2):
- skill_revisions                  content-addressed skill snapshots
- agent_releases                   immutable publish snapshots (no updated_at)
- agent_release_skills             (release_id, skill_revision_id)
- agent_release_connector_grants   (release_id, connector_instance_id, capability)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_12_agent_releases"
down_revision = "2026_07_12_published_agents"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _table_names()

    if "skill_revisions" not in existing:
        op.create_table(
            "skill_revisions",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("skill_name", sa.String(128), nullable=False),
            sa.Column("owner_user_id", sa.String(36)),
            sa.Column("visibility", sa.String(16), nullable=False, server_default="public"),
            sa.Column("content_checksum", sa.String(128), nullable=False),
            sa.Column("content_ref", sa.String(256), nullable=False),
            sa.Column("declared_connector_caps_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "skill_name",
                "owner_user_id",
                "content_checksum",
                name="uq_skill_revisions_content",
            ),
        )
        op.create_index("ix_skill_revisions_skill_name", "skill_revisions", ["skill_name"])

    if "agent_releases" not in existing:
        op.create_table(
            "agent_releases",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("agent_id", sa.String(64), nullable=False),
            sa.Column("release_no", sa.Integer(), nullable=False),
            sa.Column("agent_markdown", sa.Text(), nullable=False, server_default=""),
            sa.Column("soul_markdown", sa.Text(), nullable=False, server_default=""),
            sa.Column("model_name", sa.String(128)),
            sa.Column("tool_groups_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("quota_overrides_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("manifest_checksum", sa.String(128), nullable=False),
            sa.Column("created_by", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("agent_id", "release_no", name="uq_agent_releases_agent_release_no"),
        )
        op.create_index("ix_agent_releases_agent_id", "agent_releases", ["agent_id"])

    if "agent_release_skills" not in existing:
        op.create_table(
            "agent_release_skills",
            sa.Column("release_id", sa.String(64), sa.ForeignKey("agent_releases.id"), primary_key=True),
            sa.Column("skill_revision_id", sa.String(64), primary_key=True),
        )

    if "agent_release_connector_grants" not in existing:
        op.create_table(
            "agent_release_connector_grants",
            sa.Column("release_id", sa.String(64), sa.ForeignKey("agent_releases.id"), primary_key=True),
            sa.Column("connector_instance_id", sa.String(64), primary_key=True),
            sa.Column("capability", sa.String(80), primary_key=True),
        )


def downgrade() -> None:
    existing = _table_names()
    for table in ("agent_release_connector_grants", "agent_release_skills", "agent_releases", "skill_revisions"):
        if table in existing:
            op.drop_table(table)
    # Indexes are dropped implicitly with their tables in SQLite/batch mode.
