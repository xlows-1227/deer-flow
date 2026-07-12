"""Widen published-agent ID/FK columns and add skill_revisions.owner_scope.

Revision ID: 2026_07_12_widen_agent_ids
Revises: 2026_07_12_agent_releases
Create Date: 2026-07-12

This is a corrective migration (code-review round-2/3):

- Earlier M1 migrations created several ID/FK columns as String(32), but the
  generated ids are ``pa_``/``rel_``/``skr_`` + 32 hex (up to 36 chars), which
  PostgreSQL rejects. The base migrations have been corrected in-place for new
  databases; this migration widens the columns on databases that already
  applied the original (too-narrow) migrations.
- ``skill_revisions`` gains a non-NULL ``owner_scope`` column and its unique
  constraint is rebuilt on ``(skill_name, owner_scope, content_checksum)`` so
  that public-skill revisions (owner_user_id NULL) still deduplicate.
- Old databases may already contain DUPLICATE public revisions (the original
  constraint on a NULL-able owner_user_id did not prevent them). Before creating
  the new unique constraint this migration selects a canonical revision per
  (skill_name, owner_scope, content_checksum), rewrites
  ``agent_release_skills.skill_revision_id`` to point at the canonical row, and
  deletes the duplicates — otherwise the constraint creation would fail.

The revision id is <= 32 chars so it fits the default
``alembic_version.version_num VARCHAR(32)`` column (round-3 Critical-1).

Nullability is preserved per-column: only ``published_agents.current_release_id``
is nullable; primary keys and required FKs stay NOT NULL (round-3 Important-1).

``skill_revisions`` is NOT widened via the batch loop: a batch rebuild there
would re-create the table from the ORM metadata (which already declares the
NOT NULL ``owner_scope`` column) before ``owner_scope`` is added, violating NOT
NULL during the copy. The manual table rebuild in step 2 handles its ``id``
width together with the new constraint.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_12_widen_agent_ids"
down_revision = "2026_07_12_agent_releases"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


# Columns to widen to String(64). Tuple = (table, column, nullable_after).
# Only current_release_id is genuinely nullable; every other ID/FK keeps its
# original NOT NULL constraint (round-3 Important-1). skill_revisions.id is
# widened by the manual table rebuild below, not here (see module docstring).
_WIDEN: list[tuple[str, str, bool]] = [
    ("published_agents", "id", False),
    ("published_agents", "current_release_id", True),
    ("agent_drafts", "agent_id", False),
    ("agent_draft_skills", "agent_id", False),
    ("agent_draft_connector_grants", "agent_id", False),
    ("agent_releases", "id", False),
    ("agent_releases", "agent_id", False),
    ("agent_release_skills", "release_id", False),
    ("agent_release_skills", "skill_revision_id", False),
    ("agent_release_connector_grants", "release_id", False),
]


def upgrade() -> None:
    existing = _table_names()
    bind = op.get_bind()

    # Before any batch_alter_table rebuild, backfill NULL values in NOT NULL
    # columns. SQLite's loose mode lets rows hold NULL in a NOT NULL column, but
    # a batch rebuild creates the new table from the ORM metadata (which declares
    # NOT NULL with a server_default) and the INSERT-SELECT then enforces it.
    _backfill_not_null_columns(bind, existing)

    # 1. Widen ID/FK columns (excluding skill_revisions — handled in step 2).
    # On PostgreSQL use batch_alter_table (preserves nullability per _WIDEN).
    # On SQLite, VARCHAR length is not enforced, so the widening is a no-op for
    # correctness — but we still run it for schema consistency. To avoid the
    # batch-rebuild NOT NULL issues on SQLite, we skip the column-type widen on
    # SQLite (it has no functional effect there) and only widen on PostgreSQL.
    dialect = bind.dialect.name
    if dialect != "sqlite":
        for table, column, nullable in _WIDEN:
            if table not in existing or column not in _column_names(table):
                continue
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column(
                    column,
                    existing_type=sa.String(),
                    type_=sa.String(64),
                    nullable=nullable,
                )

    # 2. skill_revisions: add owner_scope, collapse duplicate public revisions,
    #    rebuild the unique constraint, and widen id — all via a manual table
    #    rebuild so we control the NOT NULL columns precisely.
    if "skill_revisions" not in existing:
        return

    cols = _column_names("skill_revisions")
    if "owner_scope" not in cols:
        # Add owner_scope with a server_default via raw ALTER TABLE (avoids a
        # batch rebuild whose INSERT-SELECT could violate NOT NULL on other
        # columns before we backfill them).
        bind.execute(
            sa.text(
                "ALTER TABLE skill_revisions ADD COLUMN owner_scope VARCHAR(64) NOT NULL DEFAULT 'public'"
            )
        )
        # Backfill: private skills get owner_scope = owner_user_id.
        bind.execute(
            sa.text("UPDATE skill_revisions SET owner_scope = COALESCE(owner_user_id, 'public')")
        )
        # Collapse duplicate public revisions before the unique constraint is
        # rebuilt (round-3 Critical-2).
        _collapse_duplicate_revisions(bind)

    # Backfill any NULL NOT NULL values before the table rebuild.
    bind.execute(sa.text("UPDATE skill_revisions SET visibility = 'public' WHERE visibility IS NULL"))
    bind.execute(
        sa.text("UPDATE skill_revisions SET declared_connector_caps_json = '[]' WHERE declared_connector_caps_json IS NULL")
    )

    # Rebuild the table with the widened id, owner_scope, and the new unique
    # constraint. SQLite cannot DROP/ADD CONSTRAINT in place; PostgreSQL can, so
    # branch on dialect.
    dialect = bind.dialect.name
    if dialect == "sqlite":
        bind.execute(
            sa.text(
                "CREATE TABLE _skill_revisions_new ("
                "id VARCHAR(64) PRIMARY KEY, "
                "skill_name VARCHAR(128) NOT NULL, "
                "owner_user_id VARCHAR(36), "
                "owner_scope VARCHAR(64) NOT NULL DEFAULT 'public', "
                "visibility VARCHAR(16) NOT NULL DEFAULT 'public', "
                "content_checksum VARCHAR(128) NOT NULL, "
                "content_ref VARCHAR(256) NOT NULL, "
                "declared_connector_caps_json JSON NOT NULL DEFAULT '[]', "
                "created_at DATETIME NOT NULL, "
                "CONSTRAINT uq_skill_revisions_content UNIQUE (skill_name, owner_scope, content_checksum))"
            )
        )
        bind.execute(
            sa.text(
                "INSERT INTO _skill_revisions_new "
                "(id, skill_name, owner_user_id, owner_scope, visibility, content_checksum, content_ref, declared_connector_caps_json, created_at) "
                "SELECT id, skill_name, owner_user_id, owner_scope, visibility, content_checksum, content_ref, declared_connector_caps_json, created_at "
                "FROM skill_revisions"
            )
        )
        bind.execute(sa.text("DROP TABLE skill_revisions"))
        bind.execute(sa.text("ALTER TABLE _skill_revisions_new RENAME TO skill_revisions"))
        bind.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_skill_revisions_skill_name ON skill_revisions (skill_name)"))
    else:
        # PostgreSQL.
        with op.batch_alter_table("skill_revisions") as batch_op:
            batch_op.alter_column("id", existing_type=sa.String(), type_=sa.String(64), nullable=False)
        try:
            bind.execute(sa.text("ALTER TABLE skill_revisions DROP CONSTRAINT IF EXISTS uq_skill_revisions_content"))
        except Exception:
            pass
        bind.execute(
            sa.text(
                "ALTER TABLE skill_revisions ADD CONSTRAINT uq_skill_revisions_content "
                "UNIQUE (skill_name, owner_scope, content_checksum)"
            )
        )


def _backfill_not_null_columns(bind: sa.engine.Connection, existing: set[str]) -> None:
    """Fill NULL values in NOT NULL columns before any batch table rebuild.

    SQLite's default mode tolerates NULL in a NOT NULL column on INSERT, so old
    databases may contain such rows. A batch rebuild recreates the table from
    the ORM metadata (NOT NULL + server_default) and its INSERT-SELECT copy then
    rejects those NULLs. We coalesce them to safe defaults first.
    """
    backfills = [
        # (table, column, sql_default_value)
        ("skill_revisions", "visibility", "'public'"),
        ("skill_revisions", "declared_connector_caps_json", "'[]'"),
        ("agent_releases", "agent_markdown", "''"),
        ("agent_releases", "soul_markdown", "''"),
        ("agent_releases", "tool_groups_json", "'[]'"),
        ("agent_releases", "quota_overrides_json", "'{}'"),
        ("agent_drafts", "agent_markdown", "''"),
        ("agent_drafts", "soul_markdown", "''"),
        ("agent_drafts", "tool_groups_json", "'[]'"),
        ("agent_drafts", "quota_overrides_json", "'{}'"),
        ("agent_draft_skills", "source", "'public'"),
        ("published_agents", "status", "'draft'"),
    ]
    for table, column, default in backfills:
        if table not in existing:
            continue
        cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        if column in cols:
            bind.execute(sa.text(f"UPDATE {table} SET {column} = {default} WHERE {column} IS NULL"))


def _collapse_duplicate_revisions(bind: sa.engine.Connection) -> None:
    """Select a canonical revision per (skill_name, owner_scope, checksum),
    rewrite agent_release_skills references to point at it, and delete the
    non-canonical duplicates so the new unique constraint can be created.
    """
    existing_tables = set(sa.inspect(bind).get_table_names())
    has_release_skills = "agent_release_skills" in existing_tables
    dup_groups = bind.execute(
        sa.text(
            "SELECT skill_name, owner_scope, content_checksum, COUNT(*) AS n "
            "FROM skill_revisions "
            "GROUP BY skill_name, owner_scope, content_checksum "
            "HAVING COUNT(*) > 1"
        )
    ).fetchall()

    for skill_name, owner_scope, content_checksum, _n in dup_groups:
        canonical = bind.execute(
            sa.text(
                "SELECT id FROM skill_revisions "
                "WHERE skill_name = :skill_name AND owner_scope = :owner_scope AND content_checksum = :checksum "
                "ORDER BY created_at ASC, id ASC LIMIT 1"
            ),
            {"skill_name": skill_name, "owner_scope": owner_scope, "checksum": content_checksum},
        ).scalar_one_or_none()
        if canonical is None:
            continue
        non_canonical_ids = [
            row[0]
            for row in bind.execute(
                sa.text(
                    "SELECT id FROM skill_revisions "
                    "WHERE skill_name = :skill_name AND owner_scope = :owner_scope AND content_checksum = :checksum "
                    "AND id <> :canonical"
                ),
                {
                    "skill_name": skill_name,
                    "owner_scope": owner_scope,
                    "checksum": content_checksum,
                    "canonical": canonical,
                },
            ).fetchall()
        ]
        for dup_id in non_canonical_ids:
            if has_release_skills:
                # Rewrite references to the canonical revision, then drop any
                # rows that would duplicate an existing canonical reference
                # (the PK is (release_id, skill_revision_id), so a blind UPDATE
                # from dup->canonical could collide with an existing canonical row).
                bind.execute(
                    sa.text(
                        "DELETE FROM agent_release_skills "
                        "WHERE skill_revision_id = :dup "
                        "AND EXISTS (SELECT 1 FROM agent_release_skills s "
                        "  WHERE s.release_id = agent_release_skills.release_id "
                        "  AND s.skill_revision_id = :canonical)"
                    ),
                    {"canonical": canonical, "dup": dup_id},
                )
                bind.execute(
                    sa.text(
                        "UPDATE agent_release_skills SET skill_revision_id = :canonical "
                        "WHERE skill_revision_id = :dup"
                    ),
                    {"canonical": canonical, "dup": dup_id},
                )
            bind.execute(sa.text("DELETE FROM skill_revisions WHERE id = :dup"), {"dup": dup_id})


def downgrade() -> None:
    existing = _table_names()
    if "skill_revisions" in existing and "owner_scope" in _column_names("skill_revisions"):
        with op.batch_alter_table("skill_revisions") as batch_op:
            try:
                batch_op.drop_constraint("uq_skill_revisions_content", type_="unique")
            except Exception:
                pass
            batch_op.create_unique_constraint(
                "uq_skill_revisions_content",
                ["skill_name", "owner_user_id", "content_checksum"],
            )
            batch_op.drop_column("owner_scope")
