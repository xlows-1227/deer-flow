"""Widen connector_instances.credential_ref for Fernet ciphertext.

Revision ID: 2026_07_30_widen_cred_ref
Revises: 2026_07_26_agent_ops_metrics
Create Date: 2026-07-30

Inline connector credentials encrypt ``{username, password}`` with Fernet and
store the token in ``credential_ref``. A typical token is ~160+ characters, so
``VARCHAR(128)`` truncates on PostgreSQL and create/update returns HTTP 500.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_07_30_widen_cred_ref"
down_revision = "2026_07_26_agent_ops_metrics"
branch_labels = None
depends_on = None


def _column(table_name: str, column_name: str) -> dict | None:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return None
    for column in inspector.get_columns(table_name):
        if column["name"] == column_name:
            return column
    return None


def upgrade() -> None:
    column = _column("connector_instances", "credential_ref")
    if column is None:
        return
    # Already Text / unbounded — nothing to do (e.g. fresh create_all from ORM).
    col_type = column["type"]
    length = getattr(col_type, "length", None)
    if length is None:
        return
    with op.batch_alter_table("connector_instances") as batch:
        batch.alter_column(
            "credential_ref",
            existing_type=sa.String(length=length),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    column = _column("connector_instances", "credential_ref")
    if column is None:
        return
    with op.batch_alter_table("connector_instances") as batch:
        batch.alter_column(
            "credential_ref",
            existing_type=sa.Text(),
            type_=sa.String(length=128),
            existing_nullable=False,
        )
