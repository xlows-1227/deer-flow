"""ORM rows for published-Agent quota reservations and usage accounting."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class AgentQuotaReservationRow(Base):
    """Pre-run capacity reservation with one idempotent terminal transition."""

    __tablename__ = "agent_quota_reservations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    credential_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    terminal_status: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_agent_quota_agent_status", "agent_id", "status"),
        Index("ix_agent_quota_agent_created", "agent_id", "created_at"),
    )


class AgentUsageRecordRow(Base):
    """Exactly-once terminal usage record for one published-Agent run."""

    __tablename__ = "agent_usage_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    credential_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_actor_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    release_id: Mapped[str | None] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    error_class: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("run_id", name="uq_agent_usage_run_id"),
        Index("ix_agent_usage_agent_created", "agent_id", "created_at"),
    )


class AgentQuotaRejectionRow(Base):
    """One sanitized pre-run rejection for saturation and quota operations."""

    __tablename__ = "agent_quota_rejections"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    credential_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_agent_quota_rejection_agent_created", "agent_id", "created_at"),)


__all__ = [
    "AgentQuotaRejectionRow",
    "AgentQuotaReservationRow",
    "AgentUsageRecordRow",
]
