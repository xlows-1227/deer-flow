"""ORM model for scheduled agent tasks."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class ScheduledTaskRow(Base):
    __tablename__ = "scheduled_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    repeat_type: Mapped[str] = mapped_column(String(20), nullable=False)
    execution_time: Mapped[str] = mapped_column(String(5), nullable=False)
    day_of_week: Mapped[int | None] = mapped_column(Integer)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    model_name: Mapped[str | None] = mapped_column(String(128))
    mode: Mapped[str] = mapped_column(String(20), default="pro")
    reasoning_effort: Mapped[str | None] = mapped_column(String(20))

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_status: Mapped[str | None] = mapped_column(String(20))
    last_run_thread_id: Mapped[str | None] = mapped_column(String(64))
    last_run_id: Mapped[str | None] = mapped_column(String(64))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        Index("ix_scheduled_tasks_enabled_next_run", "is_enabled", "next_run_at"),
        Index("ix_scheduled_tasks_user_created", "user_id", "created_at"),
    )
