"""Owner-session management API for Published-Agent Feishu bindings."""

from __future__ import annotations

import logging
from typing import Any, Self

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.channels.supervisor import BindingNotFoundError, FeishuSupervisor
from deerflow.persistence.agent_channel import ActiveAgentChannelConflictError, AgentChannelRepository
from deerflow.publishing.secret_store import SecretStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/published-agents/{agent_id}/channels", tags=["published-agent-channels"])


class AgentChannelCreateRequest(BaseModel):
    """Credentials for a new inactive Feishu binding."""

    model_config = ConfigDict(extra="forbid")
    app_id: str = Field(min_length=1, max_length=128)
    app_secret: SecretStr = Field(min_length=1, max_length=512)

    @field_validator("app_id")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        if not (cleaned := value.strip()):
            raise ValueError("value must not be blank")
        return cleaned

    @field_validator("app_secret", mode="before")
    @classmethod
    def _strip_secret(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
        return value


class AgentChannelUpdateRequest(BaseModel):
    """Credential rotation request for one binding."""

    model_config = ConfigDict(extra="forbid")
    app_id: str | None = Field(default=None, min_length=1, max_length=128)
    app_secret: SecretStr | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("app_id")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not (cleaned := value.strip()):
            raise ValueError("value must not be blank")
        return cleaned

    @field_validator("app_secret", mode="before")
    @classmethod
    def _strip_optional_secret(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
        return value

    @model_validator(mode="after")
    def _require_secret_for_rotation(self) -> Self:
        if self.app_secret is None:
            raise ValueError("app_secret is required for credential rotation")
        return self


def _owner_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None or getattr(request.state, "auth_method", None) != "session":
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(user.id)


def _repository(request: Request) -> AgentChannelRepository:
    value = getattr(request.app.state, "agent_channel_repo", None)
    if value is None:
        raise HTTPException(status_code=503, detail="Agent channel persistence not available")
    return value


def _secret_store(request: Request) -> SecretStore:
    value = getattr(request.app.state, "agent_channel_secret_store", None)
    if value is None:
        raise HTTPException(status_code=503, detail="Agent channel secret store not available")
    return value


def _supervisor(request: Request) -> FeishuSupervisor:
    value = getattr(request.app.state, "feishu_supervisor", None)
    if value is None:
        raise HTTPException(status_code=503, detail="Feishu supervisor not available")
    return value


async def _require_owned_agent(repository: AgentChannelRepository, agent_id: str, owner_user_id: str) -> None:
    if not await repository.owns_agent(agent_id, owner_user_id=owner_user_id):
        raise HTTPException(status_code=404, detail="Agent not found")


async def _binding_or_404(
    repository: AgentChannelRepository,
    agent_id: str,
    binding_id: str,
    owner_user_id: str,
) -> dict[str, Any]:
    row = await repository.get(agent_id, binding_id, owner_user_id=owner_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Channel binding not found")
    return row


def _safe_binding(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "channel_type": row["channel_type"],
        "app_id": row["app_id"],
        "connection_mode": row["connection_mode"],
        "status": row["status"],
        "health": row["health"],
        "health_detail": row.get("health_detail"),
        "secret_configured": bool(row.get("secret_ref")),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_started_at": row.get("last_started_at"),
    }


@router.post("", status_code=201)
async def create_agent_channel(agent_id: str, body: AgentChannelCreateRequest, request: Request) -> dict[str, Any]:
    repository = _repository(request)
    secrets = _secret_store(request)
    owner_user_id = _owner_id(request)
    await _require_owned_agent(repository, agent_id, owner_user_id)
    secret_ref = await secrets.put(body.app_secret.get_secret_value())
    try:
        created = await repository.create(
            agent_id=agent_id,
            owner_user_id=owner_user_id,
            app_id=body.app_id,
            secret_ref=secret_ref,
        )
    except BaseException:
        await secrets.delete(secret_ref)
        raise
    if created is None:
        await secrets.delete(secret_ref)
        raise HTTPException(status_code=404, detail="Agent not found")
    return _safe_binding(created)


@router.get("")
async def list_agent_channels(agent_id: str, request: Request) -> list[dict[str, Any]]:
    repository = _repository(request)
    owner_user_id = _owner_id(request)
    await _require_owned_agent(repository, agent_id, owner_user_id)
    return [_safe_binding(row) for row in await repository.list_by_agent(agent_id, owner_user_id=owner_user_id)]


@router.post("/{binding_id}/test")
async def test_agent_channel(agent_id: str, binding_id: str, request: Request) -> dict[str, Any]:
    repository = _repository(request)
    owner_user_id = _owner_id(request)
    await _binding_or_404(repository, agent_id, binding_id, owner_user_id)
    result = await _supervisor(request).test_binding(binding_id)
    return {"health": result.health, "detail": result.detail}


async def _lifecycle_action(agent_id: str, binding_id: str, request: Request, action: str) -> dict[str, Any]:
    repository = _repository(request)
    owner_user_id = _owner_id(request)
    await _binding_or_404(repository, agent_id, binding_id, owner_user_id)
    supervisor = _supervisor(request)
    try:
        await getattr(supervisor, f"{action}_binding")(binding_id)
    except ActiveAgentChannelConflictError as exc:
        raise HTTPException(status_code=409, detail="Agent already has an active Feishu binding") from exc
    except BindingNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Channel binding not found") from exc
    return _safe_binding(await _binding_or_404(repository, agent_id, binding_id, owner_user_id))


@router.post("/{binding_id}/start")
async def start_agent_channel(agent_id: str, binding_id: str, request: Request) -> dict[str, Any]:
    return await _lifecycle_action(agent_id, binding_id, request, "start")


@router.post("/{binding_id}/stop")
async def stop_agent_channel(agent_id: str, binding_id: str, request: Request) -> dict[str, Any]:
    return await _lifecycle_action(agent_id, binding_id, request, "stop")


@router.post("/{binding_id}/restart")
async def restart_agent_channel(agent_id: str, binding_id: str, request: Request) -> dict[str, Any]:
    return await _lifecycle_action(agent_id, binding_id, request, "restart")


@router.patch("/{binding_id}")
async def rotate_agent_channel_credentials(
    agent_id: str,
    binding_id: str,
    body: AgentChannelUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    repository = _repository(request)
    secrets = _secret_store(request)
    owner_user_id = _owner_id(request)
    current = await _binding_or_404(repository, agent_id, binding_id, owner_user_id)
    new_ref = await secrets.put(body.app_secret.get_secret_value())
    credentials_updated = False
    try:
        updated = await repository.update_credentials(
            agent_id,
            binding_id,
            owner_user_id=owner_user_id,
            app_id=body.app_id or str(current["app_id"]),
            secret_ref=new_ref,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Channel binding not found")
        credentials_updated = True
        if current["status"] == "active":
            await _supervisor(request).restart_binding(binding_id)
    except BaseException:
        if credentials_updated:
            try:
                await repository.update_credentials(
                    agent_id,
                    binding_id,
                    owner_user_id=owner_user_id,
                    app_id=str(current["app_id"]),
                    secret_ref=str(current["secret_ref"]),
                )
                if current["status"] == "active":
                    await _supervisor(request).restart_binding(binding_id)
            except BaseException as rollback_error:
                logger.error(
                    "Failed to roll back Feishu credential rotation",
                    extra={"binding_id": binding_id, "error_class": type(rollback_error).__name__},
                )
        await secrets.delete(new_ref)
        raise
    try:
        await secrets.delete(str(current["secret_ref"]))
    except Exception:
        logger.warning("Failed to delete superseded Feishu credential", extra={"binding_id": binding_id})
    return _safe_binding(await _binding_or_404(repository, agent_id, binding_id, owner_user_id))


@router.delete("/{binding_id}")
async def delete_agent_channel(agent_id: str, binding_id: str, request: Request) -> dict[str, bool]:
    repository = _repository(request)
    secrets = _secret_store(request)
    owner_user_id = _owner_id(request)
    current = await _binding_or_404(repository, agent_id, binding_id, owner_user_id)
    if current["status"] == "active":
        await _supervisor(request).stop_binding(binding_id)
    deleted = await repository.delete(agent_id, binding_id, owner_user_id=owner_user_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="Channel binding not found")
    await secrets.delete(str(current["secret_ref"]))
    return {"deleted": True}
