"""Stop sending reasoning_effort for Gaia models without a thinking protocol.

Onboarding-created Gaia models default to thinking_protocol="none", so pro /
thinking / ultra mode sends `reasoning_effort` but no provider-side thinking
parameter. YUMC-style gateways reject that combination with HTTP 400.

New Gaia models are created with supports_reasoning_effort=False; this revision
normalises the records that were already persisted with True.

Revision ID: 2026_11_24_gaia_reasoning_off
Revises: 2026_11_20_umodel_thinking_protocol
Create Date: 2026-11-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_11_24_gaia_reasoning_off"
down_revision = "2026_11_20_umodel_thinking_protocol"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user_models" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("user_models")}
    if not {"supports_reasoning_effort", "thinking_protocol"} <= columns:
        return

    op.execute(
        sa.text(
            "UPDATE user_models "
            "SET supports_reasoning_effort = :off "
            "WHERE provider = 'gaia' "
            "AND thinking_protocol = 'none' "
            "AND supports_reasoning_effort = :on"
        ).bindparams(off=False, on=True)
    )


def downgrade() -> None:
    # Data normalisation only; the previous value cannot be recovered safely.
    pass
