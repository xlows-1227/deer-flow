"""ORM model for skill shares — custom skills shared with specific users.

Each row represents a single "owner → user" share grant for one custom skill.
The first phase of the sharing design keeps ``skill_name`` globally unique,
so we reference ``skill_name`` rather than introducing a separate ``skill_id``
stable identifier yet.  A unique constraint on ``(skill_name, owner_user_id,
shared_with_user_id)`` prevents duplicate share grants and powers
idempotent PUT writes.

See ``docs/design/2026-06-15-skill-sharing-permissions-design-v2.md``
§权限矩阵 for the permission semantics that build on top of this table.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class SkillShareRow(Base):
    __tablename__ = "skill_shares"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # Owner user id — mirrors the owner_user_id that is embedded inside the
    # custom skill's on-disk metadata.  Denormalised here so the share
    # resolver can evaluate visibility without touching the filesystem.
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # User id receiving the share grant.  Must NOT equal owner_user_id
    # (application-enforced before insert).
    shared_with_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
    )

    __table_args__ = (
        UniqueConstraint(
            "skill_name",
            "owner_user_id",
            "shared_with_user_id",
            name="uq_skill_shares_grant",
        ),
    )
