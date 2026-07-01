from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class InviteCodeRow(Base):
    __tablename__ = "invite_codes"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    used_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_invite_codes_used", "used"),)
