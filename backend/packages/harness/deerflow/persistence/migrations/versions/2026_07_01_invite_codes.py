"""Add invite_codes table and seed 100 one-time registration codes.

Revision ID: 2026_07_01_invite_codes
Revises: 2026_06_30_user_extensions
Create Date: 2026-07-01
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "2026_07_01_invite_codes"
down_revision = "2026_06_30_user_extensions"
branch_labels = None
depends_on = None

_INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_INVITE_CODE_LENGTH = 10
_SEED_COUNT = 100


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _generate_invite_code() -> str:
    return "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(_INVITE_CODE_LENGTH))


def _generate_unique_codes(count: int) -> list[str]:
    codes: set[str] = set()
    while len(codes) < count:
        codes.add(_generate_invite_code())
    return sorted(codes)


def upgrade() -> None:
    existing = _table_names()
    if "invite_codes" not in existing:
        op.create_table(
            "invite_codes",
            sa.Column("code", sa.String(32), primary_key=True),
            sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("used_by_user_id", sa.String(36)),
            sa.Column("used_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_invite_codes_used", "invite_codes", ["used"])

    bind = op.get_bind()
    row_count = bind.execute(sa.text("SELECT COUNT(*) FROM invite_codes")).scalar_one()
    if row_count == 0:
        now = datetime.now(UTC)
        op.bulk_insert(
            sa.table(
                "invite_codes",
                sa.column("code", sa.String),
                sa.column("used", sa.Boolean),
                sa.column("created_at", sa.DateTime),
            ),
            [{"code": code, "used": False, "created_at": now} for code in _generate_unique_codes(_SEED_COUNT)],
        )


def downgrade() -> None:
    existing = _table_names()
    if "invite_codes" in existing:
        op.drop_index("ix_invite_codes_used", table_name="invite_codes")
        op.drop_table("invite_codes")
