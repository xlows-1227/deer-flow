from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ConnectorSecretStoreConfig(BaseModel):
    provider: Literal["env", "encrypted_db"] = Field(default="env")


class ConnectorDatabaseDefaultPolicyConfig(BaseModel):
    mode: Literal["read_only"] = Field(default="read_only")
    max_rows: int = Field(default=10000, ge=1)
    statement_timeout_ms: int = Field(default=30000, ge=1)
    require_limit: bool = Field(default=True)


class ConnectorDefaultPolicyConfig(BaseModel):
    database: ConnectorDatabaseDefaultPolicyConfig = Field(default_factory=ConnectorDatabaseDefaultPolicyConfig)


class ConnectorsConfig(BaseModel):
    enabled: bool = Field(default=False)
    enabled_types: list[str] = Field(default_factory=lambda: ["mysql", "starrocks"])
    secret_store: ConnectorSecretStoreConfig = Field(default_factory=ConnectorSecretStoreConfig)
    default_policy: ConnectorDefaultPolicyConfig = Field(default_factory=ConnectorDefaultPolicyConfig)
