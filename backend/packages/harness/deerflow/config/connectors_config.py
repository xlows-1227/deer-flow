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
    enabled_types: list[str] = Field(default_factory=lambda: ["mysql", "starrocks", "onedata"])
    secret_store: ConnectorSecretStoreConfig = Field(default_factory=ConnectorSecretStoreConfig)
    default_policy: ConnectorDefaultPolicyConfig = Field(default_factory=ConnectorDefaultPolicyConfig)
    # Tool results larger than this are written to a per-thread file instead of
    # being returned inline, so a single query cannot blow up the model context.
    # Results at or below the limit keep the exact legacy ``model_dump()`` shape.
    result_inline_max_chars: int = Field(
        default=50000,
        ge=1000,
        le=2000000,
        description="Maximum serialized characters of a connector tool result kept inline. Larger results are spilled to /mnt/user-data/workspace/connector_results/.",
    )
    result_preview_rows: int = Field(
        default=5,
        ge=0,
        le=50,
        description="Number of rows included in the inline preview when a connector result is spilled to a file.",
    )
    result_spill_retention_hours: int = Field(
        default=24,
        ge=0,
        description="Delete spilled result files under connector_results/ older than this many hours. 0 disables cleanup.",
    )
