"""Persistent Feishu conversation mappings and inbound event claims."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class ChannelConversationMappingRow(Base):
    """Stable mapping from one binding-scoped conversation to a thread."""

    __tablename__ = "channel_conversation_mappings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    binding_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(256), nullable=False)
    actor_scope: Mapped[str] = mapped_column(String(256), nullable=False)
    topic_id: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint(
            "binding_id",
            "chat_id",
            "actor_scope",
            "topic_id",
            name="uq_channel_mapping_scope",
        ),
        UniqueConstraint("thread_id", name="uq_channel_conversation_mappings_thread_id"),
        Index("ix_channel_mappings_binding_agent", "binding_id", "agent_id"),
    )


class ChannelEventDedupRow(Base):
    """A claimed inbound event retained for replay protection."""

    __tablename__ = "channel_event_dedup"

    binding_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, index=True)


__all__ = ["ChannelConversationMappingRow", "ChannelEventDedupRow"]
