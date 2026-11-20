from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MASKED_VALUE = "***"

# Model-side function names must start with an ASCII letter; MCP tools are
# exposed as ``{server_name}_{tool_name}`` without any sanitization.
MCP_SERVER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
MCP_TRANSPORT_TYPES = ("stdio", "sse", "http", "streamable_http")


class McpOAuthConfigSchema(BaseModel):
    enabled: bool = True
    token_url: str = ""
    grant_type: Literal["client_credentials", "refresh_token"] = "client_credentials"
    client_id: str | None = None
    client_secret: str | None = None
    refresh_token: str | None = None
    scope: str | None = None
    audience: str | None = None
    token_field: str = "access_token"
    token_type_field: str = "token_type"
    expires_in_field: str = "expires_in"
    default_token_type: str = "Bearer"
    refresh_skew_seconds: int = 60
    extra_token_params: dict[str, str] = Field(default_factory=dict)


class McpServerRecord(BaseModel):
    name: str = ""
    enabled: bool = True
    type: str = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    oauth: McpOAuthConfigSchema | None = None
    description: str = ""
    source: Literal["system", "user"] = "user"
    editable: bool = True


class McpConfigResponse(BaseModel):
    mcp_servers: dict[str, McpServerRecord] = Field(default_factory=dict)


class McpToolInfo(BaseModel):
    name: str
    description: str = ""


class McpServerToolsResponse(BaseModel):
    server: str
    tools: list[McpToolInfo] = Field(default_factory=list)
    checked_at: str


class McpServerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    enabled: bool = True
    type: Literal["stdio", "sse", "http", "streamable_http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    oauth: McpOAuthConfigSchema | None = None
    description: str = ""

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not MCP_SERVER_NAME_RE.fullmatch(normalized):
            raise ValueError("name must match ^[A-Za-z][A-Za-z0-9_-]*$ (max 32 characters)")
        return normalized


class McpServerUpdateRequest(BaseModel):
    enabled: bool | None = None
    type: Literal["stdio", "sse", "http", "streamable_http"] | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    oauth: McpOAuthConfigSchema | None = None
    description: str | None = None


class McpServerEnabledRequest(BaseModel):
    enabled: bool


class ImageProviderRecord(BaseModel):
    provider: str
    enabled: bool = False
    display_name: str = ""
    api_key: str | None = None
    has_api_key: bool = False
    base_url: str = ""
    model: str = ""
    timeout_seconds: float = 120.0
    trust_env: bool = False
    params: dict[str, Any] = Field(default_factory=dict)


class ImageConfigResponse(BaseModel):
    enabled: bool = False
    default_provider: str | None = None
    output_subdir: str = "generated-images"
    providers: dict[str, ImageProviderRecord] = Field(default_factory=dict)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class ImageProviderUpdate(BaseModel):
    enabled: bool = False
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    timeout_seconds: float = 120.0
    trust_env: bool = False
    params: dict[str, Any] = Field(default_factory=dict)


class ImageConfigUpdateRequest(BaseModel):
    enabled: bool = False
    default_provider: str | None = None
    output_subdir: str = "generated-images"
    providers: dict[str, ImageProviderUpdate] = Field(default_factory=dict)
