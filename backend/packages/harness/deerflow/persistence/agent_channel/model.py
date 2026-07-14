"""ORM model for stable Published-Agent channel bindings."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class AgentChannelRow(Base):
    """A stable integration binding attached to an Agent, never a Release."""

    __tablename__ = "agent_channels"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False, default="feishu")
    app_id: Mapped[str] = mapped_column(String(128), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    connection_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="websocket")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="inactive", index=True)
    health: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    health_detail: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_agent_channels_active",
            "agent_id",
            "channel_type",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_agent_channels_agent_status", "agent_id", "status"),
    )


__all__ = ["AgentChannelRow"]
