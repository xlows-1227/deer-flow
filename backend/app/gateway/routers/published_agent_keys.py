"""Owner-session management API for Agent-scoped credentials."""

from __future__ import annotations

import os
from typing import Any, Self

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.gateway.deps import get_agent_api_key_repo
from app.gateway.routers.published_agents import DraftService, get_draft_service
from deerflow.persistence.agent_api_key import AgentAPIKeyRepository

router = APIRouter(prefix="/api/published-agents/{agent_id}/keys", tags=["published-agent-keys"])
_QUOTA_OVERRIDE_FIELDS = frozenset(
    {
        "max_concurrent_runs",
        "daily_runs",
        "daily_tokens",
        "max_run_seconds",
        "max_tokens_per_run",
        "max_input_bytes",
        "inbound_rps",
    }
)


def _validate_quota_overrides(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("quota_overrides must be an object")
    unknown = sorted(set(value) - _QUOTA_OVERRIDE_FIELDS)
    if unknown:
        raise ValueError(f"unknown quota override fields: {', '.join(unknown)}")
    invalid = [name for name, limit in value.items() if type(limit) is not int or limit <= 0]
    if invalid:
        raise ValueError(f"quota overrides must be positive integers: {', '.join(sorted(invalid))}")
    return value


class AgentKeyCreateRequest(BaseModel):
    """Owner request for a new named Agent credential."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    quota_overrides: dict[str, int] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        if not (cleaned := value.strip()):
            raise ValueError("name must not be blank")
        return cleaned

    @field_validator("quota_overrides", mode="before")
    @classmethod
    def _quota_overrides_are_supported(cls, value: Any) -> Any:
        return _validate_quota_overrides(value)


class AgentKeyUpdateRequest(BaseModel):
    """Owner request to rename or tighten one Agent credential."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    quota_overrides: dict[str, int] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not (cleaned := value.strip()):
            raise ValueError("name must not be blank")
        return cleaned

    @field_validator("quota_overrides", mode="before")
    @classmethod
    def _quota_overrides_are_supported(cls, value: Any) -> Any:
        return _validate_quota_overrides(value)

    @model_validator(mode="after")
    def _require_change(self) -> Self:
        if self.name is None and self.quota_overrides is None:
            raise ValueError("at least one field must be supplied")
        return self


def _owner_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None or getattr(request.state, "auth_method", None) != "session":
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(user.id)


async def _require_owned_agent(request: Request, agent_id: str, service: DraftService) -> str:
    owner_user_id = _owner_id(request)
    if await service.get_agent(agent_id, owner_user_id=owner_user_id) is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return owner_user_id


def _safe_key(key: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": key["id"],
        "agent_id": key["agent_id"],
        "name": key["name"],
        "key_prefix": key["key_prefix"],
        "last_four": key["last_four"],
        "status": key["status"],
        "quota_overrides": key.get("quota_overrides") or {},
        "created_at": key["created_at"],
        "last_used_at": key.get("last_used_at"),
        "expires_at": key.get("expires_at"),
        "revoked_at": key.get("revoked_at"),
        "rotation_of": key.get("rotation_of"),
    }


def _rotation_overlap_seconds() -> int:
    try:
        return max(0, int(os.environ.get("AGENT_API_KEY_ROTATION_OVERLAP_SECONDS", "86400")))
    except ValueError:
        return 86400


@router.post("", status_code=201)
async def create_agent_key(
    agent_id: str,
    body: AgentKeyCreateRequest,
    request: Request,
    repository: AgentAPIKeyRepository = Depends(get_agent_api_key_repo),
    service: DraftService = Depends(get_draft_service),
) -> dict[str, Any]:
    """Create an Agent credential and reveal its plaintext exactly once."""
    owner_user_id = await _require_owned_agent(request, agent_id, service)
    created = await repository.create(agent_id=agent_id, owner_user_id=owner_user_id, name=body.name, quota_overrides=body.quota_overrides)
    return {
        **_safe_key(created),
        "api_key": created["api_key"],
        "warning": "This API key will not be shown again.",
    }


@router.get("")
async def list_agent_keys(
    agent_id: str,
    request: Request,
    repository: AgentAPIKeyRepository = Depends(get_agent_api_key_repo),
    service: DraftService = Depends(get_draft_service),
) -> list[dict[str, Any]]:
    """List safe metadata for the current owner's Agent credentials."""
    owner_user_id = await _require_owned_agent(request, agent_id, service)
    return [_safe_key(item) for item in await repository.list_by_agent(agent_id, owner_user_id=owner_user_id)]


@router.post("/{key_id}/rotate", status_code=201)
async def rotate_agent_key(
    agent_id: str,
    key_id: str,
    request: Request,
    repository: AgentAPIKeyRepository = Depends(get_agent_api_key_repo),
    service: DraftService = Depends(get_draft_service),
) -> dict[str, Any]:
    """Rotate a credential while preserving the configured overlap window."""
    owner_user_id = await _require_owned_agent(request, agent_id, service)
    rotated = await repository.rotate(agent_id, key_id, owner_user_id=owner_user_id, overlap_seconds=_rotation_overlap_seconds())
    if rotated is None:
        raise HTTPException(status_code=404, detail="Key not found")
    return {
        **_safe_key(rotated),
        "api_key": rotated["api_key"],
        "warning": "This API key will not be shown again.",
    }


@router.post("/{key_id}/revoke")
async def revoke_agent_key(
    agent_id: str,
    key_id: str,
    request: Request,
    repository: AgentAPIKeyRepository = Depends(get_agent_api_key_repo),
    service: DraftService = Depends(get_draft_service),
) -> dict[str, bool]:
    """Immediately revoke one Agent credential."""
    owner_user_id = await _require_owned_agent(request, agent_id, service)
    if not await repository.revoke(agent_id, key_id, owner_user_id=owner_user_id):
        raise HTTPException(status_code=404, detail="Key not found")
    return {"revoked": True}


@router.patch("/{key_id}")
async def update_agent_key(
    agent_id: str,
    key_id: str,
    body: AgentKeyUpdateRequest,
    request: Request,
    repository: AgentAPIKeyRepository = Depends(get_agent_api_key_repo),
    service: DraftService = Depends(get_draft_service),
) -> dict[str, Any]:
    """Update a credential name or its stricter quota overrides."""
    owner_user_id = await _require_owned_agent(request, agent_id, service)
    updated = await repository.update(
        agent_id,
        key_id,
        owner_user_id=owner_user_id,
        name=body.name,
        quota_overrides=body.quota_overrides,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Key not found")
    return _safe_key(updated)
