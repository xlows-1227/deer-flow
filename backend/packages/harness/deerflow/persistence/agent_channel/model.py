"""ORM model for stable Published-Agent channel bindings."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, false, text
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
    delete_previous_status: Mapped[str | None] = mapped_column(String(16))
    runtime_lease_token: Mapped[str | None] = mapped_column(String(64))
    runtime_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runtime_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    runtime_stop_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    secret_cleanup_ref: Mapped[str | None] = mapped_column(String(128))
    secret_cleanup_reason: Mapped[str | None] = mapped_column(String(32))
    secret_cleanup_not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotation_previous_secret_ref: Mapped[str | None] = mapped_column(String(128))
    health: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    health_detail: Mapped[str | None] = mapped_column(String(512))
    health_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
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
        Index(
            "uq_agent_channels_app_id_active",
            "app_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_agent_channels_agent_status", "agent_id", "status"),
    )


class AgentChannelSecretIngestRow(Base):
    """Database-owned ciphertext awaiting atomic transfer or janitor erase."""

    __tablename__ = "agent_channel_secret_ingests"

    secret_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    binding_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved", server_default="reserved")
    writer_token: Mapped[str | None] = mapped_column(String(64))
    writer_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    writer_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[str | None] = mapped_column(String(64))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        Index(
            "ix_agent_channel_secret_ingests_due",
            "state",
            "not_before",
            "writer_lease_expires_at",
            "claim_expires_at",
        ),
    )


__all__ = ["AgentChannelRow", "AgentChannelSecretIngestRow"]
