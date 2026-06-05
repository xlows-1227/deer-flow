from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class ConnectorInstanceRow(Base):
    __tablename__ = "connector_instances"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    owner_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(160))
    type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    credential_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    credential_provider: Mapped[str] = mapped_column(String(40), nullable=False, default="env")
    # Plaintext account name for inline credentials. Kept separate from the
    # encrypted ``credential_ref`` so the edit form can show who this
    # connector connects as without needing to decrypt the secret blob.
    credential_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    default_policy_json: Mapped[dict] = mapped_column(JSON, default=dict)
    health_json: Mapped[dict] = mapped_column(JSON, default=dict)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_connector_instances_owner_type_status", ConnectorInstanceRow.owner_id, ConnectorInstanceRow.type, ConnectorInstanceRow.status)
Index("ix_connector_instances_tenant_type_status", ConnectorInstanceRow.tenant_id, ConnectorInstanceRow.type, ConnectorInstanceRow.status)


class ConnectorGrantRow(Base):
    __tablename__ = "connector_grants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connector_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    capabilities_json: Mapped[list] = mapped_column(JSON, default=list)
    policy_override_json: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


Index("ix_connector_grants_subject", ConnectorGrantRow.subject_type, ConnectorGrantRow.subject_id)


class ConnectorMetadataCacheRow(Base):
    __tablename__ = "connector_metadata_cache"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connector_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConnectorAuditLogRow(Base):
    __tablename__ = "connector_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connector_id: Mapped[str | None] = mapped_column(String(64), index=True)
    connector_type: Mapped[str | None] = mapped_column(String(40), index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(128))
    skill_name: Mapped[str | None] = mapped_column(String(128))
    capability: Mapped[str] = mapped_column(String(80), nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    request_summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
