"""Compatibility stub for the original widen migration (superseded).

Revision ID: 2026_07_12_widen_published_agent_ids
Revises: 2026_07_12_agent_releases
Create Date: 2026-07-12

This revision was the original corrective migration shipped in the third
review round. Its revision id (36 chars) exceeds the default
``alembic_version.version_num VARCHAR(32)`` column, so PostgreSQL cannot stamp
it — but SQLite does not enforce VARCHAR length, so SQLite databases that
applied the third-round code have this value in ``alembic_version``.

The actual widening + owner_scope + duplicate-collapse work now lives in the
short-id revision ``2026_07_12_widen_agent_ids`` (26 chars, PostgreSQL-safe).
This file is retained as a **compatibility stub** so the migration graph still
recognises the old revision id and those SQLite databases can advance to the
current head instead of being stranded (fourth-review Critical-1).

The stub's ``upgrade()`` is NOT empty: it widens ``alembic_version.version_num``
to ``VARCHAR(64)`` so PostgreSQL can stamp this 36-char revision id before
advancing to the short-id child (fifth-review Critical-1). Without this, a new
PostgreSQL database would fail at the stamp step and never reach the current
head.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_12_widen_published_agent_ids"
down_revision = "2026_07_12_agent_releases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Widen alembic_version.version_num so PostgreSQL can stamp this 36-char
    # revision id (the default column is VARCHAR(32)). SQLite ignores VARCHAR
    # length so this is a no-op there. The short-id child migration performs
    # the actual schema work; this stub only ensures the version stamp fits.
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        bind.execute(sa.text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)"))
    elif dialect == "sqlite":
        # SQLite does not enforce VARCHAR length, but batch-alter for consistency.
        with op.batch_alter_table("alembic_version") as batch_op:
            batch_op.alter_column("version_num", existing_type=sa.String(32), type_=sa.String(64))


def downgrade() -> None:
    # No-op: narrowing the version column could truncate valid revision ids.
    pass
