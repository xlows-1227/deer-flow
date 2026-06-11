"""Versioned request and response models for External API V1."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_MAX_METADATA_BYTES = 32 * 1024


def validate_external_name(value: str, *, field_name: str = "name", max_length: int = 128) -> str:
    value = value.strip()
    if not value or len(value) > max_length or not _NAME_RE.fullmatch(value):
        raise ValueError(f"{field_name} must contain only letters, digits, hyphens, and underscores")
    return value


def _validate_metadata(value: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must contain only JSON-compatible values") from exc
    if len(encoded) > _MAX_METADATA_BYTES:
        raise ValueError("metadata must not exceed 32 KB")
    return value


class ExternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExternalConversationCreateRequest(ExternalModel):
    source: str = Field(default="default", min_length=1, max_length=64)
    external_conversation_id: str | None = Field(default=None, min_length=1, max_length=256)
    agent: str = Field(default="lead_agent", min_length=1, max_length=128)
    default_skill: str | None = Field(default=None, min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source")
    @classmethod
    def _source_is_safe(cls, value: str) -> str:
        value = value.strip()
        if not _SOURCE_RE.fullmatch(value):
            raise ValueError("source contains unsupported characters")
        return value

    @field_validator("agent", "default_skill")
    @classmethod
    def _names_are_safe(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return validate_external_name(value, field_name=info.field_name)

    @field_validator("metadata")
    @classmethod
    def _metadata_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)


class ExternalRunCreateRequest(ExternalModel):
    message: str = Field(min_length=1, max_length=200_000)
    skill: str | None = Field(default=None, min_length=1, max_length=128)
    mode: Literal["standard", "thinking", "pro", "ultra", "flash"] = "standard"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def _message_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value

    @field_validator("skill")
    @classmethod
    def _skill_is_safe(cls, value: str | None) -> str | None:
        return validate_external_name(value, field_name="skill") if value is not None else None

    @field_validator("metadata")
    @classmethod
    def _metadata_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)


class ExternalConversationResponse(ExternalModel):
    request_id: str | None = None
    conversation_id: str
    status: Literal["active", "closed"]
    agent: str
    default_skill: str | None = None
    source: str = "default"
    external_conversation_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ExternalRunResponse(ExternalModel):
    request_id: str | None = None
    run_id: str
    conversation_id: str
    skill: str | None = None
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    answer: str | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExternalSkillSummary(ExternalModel):
    name: str
    description: str = ""
    display_name: str | None = None
    description_zh: str | None = None


class ExternalSkillsResponse(ExternalModel):
    request_id: str | None = None
    skills: list[ExternalSkillSummary]
