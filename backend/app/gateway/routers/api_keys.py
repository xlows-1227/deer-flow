"""Browser-session management endpoints for a user's External API Key."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.gateway.deps import get_api_key_repo, get_config
from app.gateway.external.models import validate_external_name
from app.gateway.external.service import APIKeyService
from deerflow.config.app_config import AppConfig
from deerflow.persistence.api_key import APIKeyRepository
from deerflow.skills.storage import get_or_new_skill_storage

router = APIRouter(prefix="/api/v1/api-keys", tags=["external-api-keys"])


class APIKeyPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_skills: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("allowed_skills")
    @classmethod
    def _validate_skills(cls, skills: list[str]) -> list[str]:
        return sorted({validate_external_name(skill, field_name="skill") for skill in skills})


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None or getattr(request.state, "auth_method", None) != "session":
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(user.id)


def _set_audit_key(request: Request, key_id: str | None = None) -> None:
    request.state.external_audit_resource_type = "api_key"
    request.state.external_audit_resource_id = key_id


def _safe_key(key: dict[str, Any]) -> dict[str, Any]:
    return {
        "exists": True,
        "id": key["id"],
        "name": key["name"],
        "masked_key": f"{key['key_prefix']}...****{key['last_four']}",
        "status": key["status"],
        "scopes": key.get("scopes") or [],
        "allowed_skills": key.get("allowed_skills") or [],
        "created_at": key["created_at"],
        "last_used_at": key.get("last_used_at"),
        "expires_at": key.get("expires_at"),
    }


def _ensure_skills_enabled(skills: list[str], config: AppConfig) -> None:
    if not skills:
        return
    enabled = {skill.name for skill in get_or_new_skill_storage(app_config=config).load_skills(enabled_only=True)}
    unavailable = sorted(set(skills) - enabled)
    if unavailable:
        raise HTTPException(status_code=422, detail={"code": "skill_not_available", "skills": unavailable})


@router.get("/current")
async def get_current_api_key(request: Request, repository: APIKeyRepository = Depends(get_api_key_repo)) -> dict[str, Any]:
    key = await repository.get_current_for_user(_user_id(request))
    _set_audit_key(request, key["id"] if key else None)
    return _safe_key(key) if key else {"exists": False}


@router.post("/current/rotate", status_code=201)
async def rotate_current_api_key(
    request: Request,
    policy: APIKeyPolicyRequest | None = None,
    repository: APIKeyRepository = Depends(get_api_key_repo),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    _ensure_skills_enabled(policy.allowed_skills if policy else [], config)
    result = await APIKeyService(repository).rotate(
        user_id=_user_id(request),
        allowed_skills=policy.allowed_skills if policy else [],
    )
    _set_audit_key(request, result["id"])
    return {
        "api_key": result["api_key"],
        "id": result["id"],
        "created_at": result["created_at"],
        "warning": "This API key will not be shown again.",
    }


@router.put("/current/policy")
async def update_current_api_key_policy(
    policy: APIKeyPolicyRequest,
    request: Request,
    repository: APIKeyRepository = Depends(get_api_key_repo),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    _ensure_skills_enabled(policy.allowed_skills, config)
    result = await APIKeyService(repository).update_policy(_user_id(request), policy.allowed_skills)
    if result is None:
        raise HTTPException(status_code=404, detail="No active API Key")
    _set_audit_key(request, result["id"])
    return _safe_key(result)


@router.delete("/current")
async def revoke_current_api_key(request: Request, repository: APIKeyRepository = Depends(get_api_key_repo)) -> dict[str, bool]:
    _set_audit_key(request)
    await APIKeyService(repository).revoke(_user_id(request))
    return {"revoked": True}
