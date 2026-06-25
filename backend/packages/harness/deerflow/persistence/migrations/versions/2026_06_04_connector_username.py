"""add credential_username to connector_instances

Revision ID: 2026_06_04_connector_username
Revises:
Create Date: 2026-06-04 18:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "2026_06_04_connector_username"
down_revision = None
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    # Add the plaintext account name column for inline credentials. Kept
    # separate from the encrypted ``credential_ref`` so the edit form can
    # show who this connector connects as without needing to decrypt the
    # secret blob. Nullable because env / encrypted_db providers don't
    # carry a username.
    columns = _column_names("connector_instances")
    if columns and "credential_username" not in columns:
        op.add_column(
            "connector_instances",
            sa.Column("credential_username", sa.String(length=128), nullable=True),
        )


def downgrade() -> None:
    if "credential_username" in _column_names("connector_instances"):
        op.drop_column("connector_instances", "credential_username")
