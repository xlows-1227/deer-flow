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
This file is retained as a **no-op stub** so the migration graph still
recognises the old revision id and those SQLite databases can advance to the
current head instead of being stranded (fourth-review Critical-1).

``upgrade``/``downgrade`` do nothing: the short-id migration performs the real
schema work and is idempotent (it guards every step on column/table existence),
so re-running it from either parent revision is safe.
"""

from __future__ import annotations

revision = "2026_07_12_widen_published_agent_ids"
down_revision = "2026_07_12_agent_releases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op: real work is in 2026_07_12_widen_agent_ids (the short-id child).
    pass


def downgrade() -> None:
    # No-op: cannot reverse a no-op.
    pass
