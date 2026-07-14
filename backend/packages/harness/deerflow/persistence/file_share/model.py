"""ORM model for read-only file shares between registered users."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class FileShareRow(Base):
    __tablename__ = "file_shares"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipient_user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    # Empty string identifies library files. Keeping this non-null makes the
    # cross-database uniqueness constraint reliable (NULL values are otherwise
    # considered distinct by both SQLite and PostgreSQL).
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "recipient_user_id",
            "source_type",
            "source_path",
            "thread_id",
            name="uq_file_shares_recipient_source",
        ),
    )
