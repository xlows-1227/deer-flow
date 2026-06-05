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


def upgrade() -> None:
    # Add the plaintext account name column for inline credentials. Kept
    # separate from the encrypted ``credential_ref`` so the edit form can
    # show who this connector connects as without needing to decrypt the
    # secret blob. Nullable because env / encrypted_db providers don't
    # carry a username.
    op.add_column(
        "connector_instances",
        sa.Column("credential_username", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connector_instances", "credential_username")
