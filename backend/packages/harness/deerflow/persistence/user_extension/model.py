from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class UserMcpServerRow(Base):
    __tablename__ = "user_mcp_servers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="stdio")
    command: Mapped[str | None] = mapped_column(String(512))
    args: Mapped[list | None] = mapped_column(JSON)
    url: Mapped[str | None] = mapped_column(String(1024))
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    secrets_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_mcp_servers_user_name"),)


class UserMcpServerStateRow(Base):
    __tablename__ = "user_mcp_server_states"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    server_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class UserImageSettingsRow(Base):
    __tablename__ = "user_image_settings"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_provider: Mapped[str | None] = mapped_column(String(64))
    output_subdir: Mapped[str] = mapped_column(String(256), nullable=False, default="generated-images")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class UserImageProviderRow(Base):
    __tablename__ = "user_image_providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    api_key_ref: Mapped[str | None] = mapped_column(String(512))
    api_key_last_four: Mapped[str | None] = mapped_column(String(4))
    base_url: Mapped[str | None] = mapped_column(String(512))
    model: Mapped[str | None] = mapped_column(String(128))
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=120.0)
    trust_env: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    params: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_user_image_providers_user_provider"),)


Index("ix_user_image_providers_user_enabled", UserImageProviderRow.user_id, UserImageProviderRow.enabled)
