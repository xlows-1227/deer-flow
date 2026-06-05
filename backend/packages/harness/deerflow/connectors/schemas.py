from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

DATABASE_QUERY = "database.query"
DATABASE_SCHEMA_INSPECT = "database.schema.inspect"
DATABASE_TABLE_SAMPLE = "database.table.sample"


class ConnectorTypeDefinition(BaseModel):
    type: str
    category: str
    display_name: str
    adapter: str
    auth_modes: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    credential_schema: dict[str, Any] = Field(default_factory=dict)
    default_policy: dict[str, Any] = Field(default_factory=dict)

    def safe_dump(self) -> dict[str, Any]:
        data = self.model_dump()
        data.pop("adapter", None)
        return data


class ConnectorCredentialRef(BaseModel):
    provider: Literal["env", "encrypted_db", "inline"] = "env"
    ref: str | None = None
    username: str | None = None
    password: str | None = None


class DatabasePolicy(BaseModel):
    mode: Literal["read_only"] = "read_only"
    allow_write: bool = False
    allow_ddl: bool = False
    allowed_schemas: list[str] | None = None
    allowed_tables: list[str] | None = None
    blocked_tables: list[str] = Field(default_factory=list)
    max_rows: int = Field(default=10000, ge=1)
    statement_timeout_ms: int = Field(default=30000, ge=1)
    allow_multi_statement: bool = False
    require_limit: bool = True
    pii_policy: str = "mask"


class ConnectorInstance(BaseModel):
    id: str
    tenant_id: str | None = None
    owner_id: str | None = None
    name: str
    display_name: str | None = None
    type: str
    status: str = "active"
    config: dict[str, Any] = Field(default_factory=dict)
    credential: ConnectorCredentialRef
    default_policy: dict[str, Any] = Field(default_factory=dict)
    health: dict[str, Any] = Field(default_factory=dict)
    last_tested_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ConnectorGrant(BaseModel):
    id: str
    connector_id: str
    subject_type: Literal["user", "skill", "agent", "thread"]
    subject_id: str
    capabilities: list[str]
    policy_override: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None
    created_by: str | None = None


class ConnectorRuntimeContext(BaseModel):
    user_id: str | None = None
    tenant_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    skill_name: str | None = None
    connector_ids: list[str] | None = None


class AuthorizationDecision(BaseModel):
    allow: bool
    effective_policy: dict[str, Any] = Field(default_factory=dict)
    reason: str
    matched_grant_id: str | None = None


class ConnectorTestResult(BaseModel):
    status: Literal["ok", "error"]
    latency_ms: int | None = None
    message: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class ConnectorMetadata(BaseModel):
    schemas: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    cached_at: datetime | None = None


class QueryColumn(BaseModel):
    name: str
    type: str | None = None


class QueryResult(BaseModel):
    columns: list[QueryColumn]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False
    elapsed_ms: int | None = None


class SqlSafetyResult(BaseModel):
    sql: str
    tables: list[str] = Field(default_factory=list)
    sql_hash: str
    normalized_preview: str
