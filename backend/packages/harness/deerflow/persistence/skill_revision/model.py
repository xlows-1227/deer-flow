"""ORM model for skill_revisions — immutable, content-deduplicated snapshots.

A ``SkillRevisionRow`` pins a specific, immutable version of a Skill at publish
time (design doc §7.4). Uniqueness on ``(skill_name, owner_user_id,
content_checksum)`` means identical content is reused across publishes, so an
unchanged skill never spuriously creates a new revision. ``owner_user_id`` is
NULL for platform-public skills.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class SkillRevisionRow(Base):
    """An immutable, content-addressed snapshot of a Skill used by a release.

    ``content_ref`` is an opaque pointer into the immutable content store
    (see ``deerflow.publishing.content_store``); the row never stores skill
    source bytes directly. ``declared_connector_caps_json`` records the
    connector capabilities the skill declares it needs, so the resolver can
    intersect them with the release's grants at run time.
    """

    __tablename__ = "skill_revisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(36))
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="public")
    content_checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    content_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    declared_connector_caps_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "skill_name",
            "owner_user_id",
            "content_checksum",
            name="uq_skill_revisions_content",
        ),
    )
