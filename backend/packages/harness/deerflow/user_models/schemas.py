from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProviderType = Literal["openai", "anthropic"]

PROVIDER_USE_MAP: dict[ProviderType, str] = {
    "openai": "langchain_openai:ChatOpenAI",
    "anthropic": "langchain_anthropic:ChatAnthropic",
}

DEFAULT_BASE_URLS: dict[ProviderType, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}

MASKED_API_KEY = "***"


class UserModelRecord(BaseModel):
    id: str
    user_id: str
    name: str
    display_name: str | None = None
    provider: ProviderType
    model: str
    base_url: str | None = None
    enabled: bool = True
    has_api_key: bool = False
    api_key_last_four: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class UserModelCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    display_name: str | None = Field(default=None, max_length=160)
    provider: ProviderType
    model: str = Field(..., min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=512)
    api_key: str | None = Field(default=None, max_length=512)
    enabled: bool = True


class UserModelUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    display_name: str | None = Field(default=None, max_length=160)
    provider: ProviderType | None = None
    model: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=512)
    api_key: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None


class UserModelListResponse(BaseModel):
    models: list[UserModelRecord] = Field(default_factory=list)
