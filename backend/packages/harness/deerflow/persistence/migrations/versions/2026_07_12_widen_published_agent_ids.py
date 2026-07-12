"""Widen published-agent ID/FK columns and add skill_revisions.owner_scope.

Revision ID: 2026_07_12_widen_published_agent_ids
Revises: 2026_07_12_agent_releases
Create Date: 2026-07-12

This is a corrective migration (code-review round-2 Critical-1 / Important-2):

- Earlier M1 migrations created several ID/FK columns as String(32), but the
  generated ids are ``pa_``/``rel_``/``skr_`` + 32 hex (up to 36 chars), which
  PostgreSQL rejects. The base migrations have been corrected in-place for new
  databases; this migration widens the columns on databases that already
  applied the original (too-narrow) migrations.
- ``skill_revisions`` gains a non-NULL ``owner_scope`` column and its unique
  constraint is rebuilt on ``(skill_name, owner_scope, content_checksum)`` so
  that public-skill revisions (owner_user_id NULL) still deduplicate. Under the
  default SQL unique semantics multiple NULLs are distinct, so the original
  constraint on ``(skill_name, owner_user_id, content_checksum)`` did not
  protect public-skill dedup.

The migration is idempotent and works on both SQLite (batch mode) and PostgreSQL.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_12_widen_published_agent_ids"
down_revision = "2026_07_12_agent_releases"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


# Columns to widen to String(64): (table, column)
_WIDEN: list[tuple[str, str]] = [
    ("published_agents", "id"),
    ("published_agents", "current_release_id"),
    ("agent_drafts", "agent_id"),
    ("agent_draft_skills", "agent_id"),
    ("agent_draft_connector_grants", "agent_id"),
    ("agent_releases", "id"),
    ("agent_releases", "agent_id"),
    ("agent_release_skills", "release_id"),
    ("agent_release_skills", "skill_revision_id"),
    ("agent_release_connector_grants", "release_id"),
    ("skill_revisions", "id"),
]


def upgrade() -> None:
    existing = _table_names()

    # 1. Widen ID/FK columns on any table that already exists from an earlier
    #    (too-narrow) migration application. alter_column is portable across
    #    SQLite (batch) and PostgreSQL.
    for table, column in _WIDEN:
        if table not in existing:
            continue
        if column not in _column_names(table):
            continue
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(column, existing_type=sa.String(), type_=sa.String(64), nullable=True)

    # 2. Add skill_revisions.owner_scope and rebuild the dedup unique constraint
    #    so public-skill revisions (NULL owner_user_id) still deduplicate.
    if "skill_revisions" in existing and "owner_scope" not in _column_names("skill_revisions"):
        with op.batch_alter_table("skill_revisions") as batch_op:
            batch_op.add_column(sa.Column("owner_scope", sa.String(64), nullable=False, server_default="public"))
            # Backfill: private skills get owner_scope = owner_user_id, public stays 'public'.
        bind = op.get_bind()
        bind.execute(
            sa.text(
                "UPDATE skill_revisions SET owner_scope = COALESCE(owner_user_id, 'public')"
            )
        )
        with op.batch_alter_table("skill_revisions") as batch_op:
            batch_op.drop_constraint("uq_skill_revisions_content", type_="unique")
            batch_op.create_unique_constraint(
                "uq_skill_revisions_content",
                ["skill_name", "owner_scope", "content_checksum"],
            )


def downgrade() -> None:
    existing = _table_names()
    # Best-effort downgrade: narrow is intentionally not reversed (data may
    # already exceed String(32)); only the owner_scope addition is reverted.
    if "skill_revisions" in existing and "owner_scope" in _column_names("skill_revisions"):
        with op.batch_alter_table("skill_revisions") as batch_op:
            batch_op.drop_constraint("uq_skill_revisions_content", type_="unique")
            batch_op.create_unique_constraint(
                "uq_skill_revisions_content",
                ["skill_name", "owner_user_id", "content_checksum"],
            )
            batch_op.drop_column("owner_scope")
