"""ORM model for permanent, revocable public HTML publications."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class FilePublicationRow(Base):
    __tablename__ = "file_publications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    public_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "thread_id",
            "source_path",
            name="uq_file_publications_owner_source",
        ),
    )
