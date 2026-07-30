"""Owner-session management API for Published-Agent Feishu bindings."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Self
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.channels.supervisor import BindingCleanupPendingError, BindingNotFoundError, BindingStartError, FeishuSupervisor
from deerflow.persistence.agent_channel import ActiveAgentChannelConflictError, AgentChannelRepository, AgentChannelSecretCleanupPendingError
from deerflow.publishing.feishu_credentials import FeishuCredentials, encode_feishu_credentials
from deerflow.publishing.secret_store import SecretStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/published-agents/{agent_id}/channels", tags=["published-agent-channels"])
SECRET_INGEST_WRITER_LEASE_SECONDS = 30.0


class AgentChannelCreateRequest(BaseModel):
    """Credentials for a new inactive Feishu binding."""

    model_config = ConfigDict(extra="forbid")
    app_id: str = Field(min_length=1, max_length=128)
    app_secret: SecretStr = Field(min_length=1, max_length=512)
    verification_token: SecretStr = Field(min_length=1, max_length=512)
    encrypt_key: SecretStr | None = Field(default=None, max_length=512)

    @field_validator("app_id")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        if not (cleaned := value.strip()):
            raise ValueError("value must not be blank")
        return cleaned

    @field_validator("app_secret", "verification_token", "encrypt_key", mode="before")
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
    verification_token: SecretStr | None = Field(default=None, min_length=1, max_length=512)
    encrypt_key: SecretStr | None = Field(default=None, max_length=512)

    @field_validator("app_id")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not (cleaned := value.strip()):
            raise ValueError("value must not be blank")
        return cleaned

    @field_validator("app_secret", "verification_token", "encrypt_key", mode="before")
    @classmethod
    def _strip_optional_secret(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
        return value

    @model_validator(mode="after")
    def _require_secret_for_rotation(self) -> Self:
        if self.app_secret is None or self.verification_token is None:
            raise ValueError("app_secret and verification_token are required for credential rotation")
        return self


def _secret_payload(
    *,
    app_secret: SecretStr,
    verification_token: SecretStr,
    encrypt_key: SecretStr | None,
) -> str:
    """Build the versioned value encrypted by the configured SecretStore."""
    return encode_feishu_credentials(
        FeishuCredentials(
            app_secret=app_secret.get_secret_value(),
            verification_token=verification_token.get_secret_value(),
            encrypt_key=encrypt_key.get_secret_value() if encrypt_key is not None else "",
        )
    )


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
    """Reject access unless the session owner owns the stable Agent."""
    if not await repository.owns_agent(agent_id, owner_user_id=owner_user_id):
        raise HTTPException(status_code=404, detail="Agent not found")


async def _binding_or_404(
    repository: AgentChannelRepository,
    agent_id: str,
    binding_id: str,
    owner_user_id: str,
) -> dict[str, Any]:
    """Resolve one binding through an owner-scoped repository lookup."""
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


async def _discard_unstaged_secret(
    repository: AgentChannelRepository,
    secrets: SecretStore,
    secret_ref: str,
    *,
    agent_id: str,
    binding_id: str,
    owner_user_id: str,
) -> None:
    """CAS and erase a route-local ingest only if DB transfer has not won."""
    claim_token = uuid4().hex
    try:
        claimed = await repository.claim_owned_secret_ingest_cleanup(
            secret_ref,
            agent_id=agent_id,
            binding_id=binding_id,
            owner_user_id=owner_user_id,
            claim_token=claim_token,
        )
        if claimed is None:
            return
        await secrets.delete(secret_ref)
        completed = await repository.complete_owned_secret_ingest_cleanup(
            secret_ref,
            agent_id=agent_id,
            binding_id=binding_id,
            owner_user_id=owner_user_id,
            claim_token=claim_token,
        )
        if not completed:
            raise RuntimeError("Feishu credential ingest cleanup acknowledgement failed")
    except Exception:
        logger.warning("Failed to erase unstaged Feishu credential", extra={"binding_id": binding_id})


async def _put_database_owned_secret(
    repository: AgentChannelRepository,
    secrets: SecretStore,
    secret: str,
    *,
    agent_id: str,
    binding_id: str,
    owner_user_id: str,
) -> str:
    """Fence and heartbeat one writer until ciphertext becomes transferable."""
    secret_ref = secrets.new_ref()
    reserved = await repository.reserve_secret_ingest(
        agent_id=agent_id,
        binding_id=binding_id,
        owner_user_id=owner_user_id,
        secret_ref=secret_ref,
    )
    if reserved is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    writer_token = uuid4().hex
    writing = await repository.begin_secret_ingest_write(
        secret_ref,
        agent_id=agent_id,
        binding_id=binding_id,
        owner_user_id=owner_user_id,
        writer_token=writer_token,
        lease_seconds=SECRET_INGEST_WRITER_LEASE_SECONDS,
    )
    if writing is None:
        raise RuntimeError("Feishu credential ingest writer could not be claimed")
    writer_generation = int(writing["writer_generation"])

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(SECRET_INGEST_WRITER_LEASE_SECONDS / 3)
            try:
                renewed = await repository.renew_secret_ingest_write(
                    secret_ref,
                    writer_token=writer_token,
                    writer_generation=writer_generation,
                    lease_seconds=SECRET_INGEST_WRITER_LEASE_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to renew Feishu credential writer lease", extra={"binding_id": binding_id})
                continue
            if not renewed:
                return

    write_task = asyncio.create_task(secrets.put_reserved(secret_ref, secret))
    heartbeat_task = asyncio.create_task(heartbeat())
    cancelled = False
    try:
        try:
            try:
                await asyncio.shield(write_task)
            except asyncio.CancelledError:
                # ``put_reserved`` may be backed by a worker thread. Do not
                # abandon its DB owner while that write can still land later.
                cancelled = True
                await asyncio.shield(write_task)
        except BaseException:
            await repository.fail_secret_ingest_write(
                secret_ref,
                writer_token=writer_token,
                writer_generation=writer_generation,
            )
            raise
        ready = await repository.complete_secret_ingest_write(
            secret_ref,
            writer_token=writer_token,
            writer_generation=writer_generation,
        )
        if ready is None:
            raise RuntimeError("Feishu credential ingest writer lost ownership before transfer")
        if cancelled:
            raise asyncio.CancelledError()
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
    return secret_ref


@router.post("", status_code=201)
async def create_agent_channel(agent_id: str, body: AgentChannelCreateRequest, request: Request) -> dict[str, Any]:
    """Create an inactive binding and encrypt its complete credential bundle."""
    repository = _repository(request)
    secrets = _secret_store(request)
    owner_user_id = _owner_id(request)
    await _require_owned_agent(repository, agent_id, owner_user_id)
    binding_id = f"ach_{uuid4().hex}"
    secret_ref = await _put_database_owned_secret(
        repository,
        secrets,
        _secret_payload(
            app_secret=body.app_secret,
            verification_token=body.verification_token,
            encrypt_key=body.encrypt_key,
        ),
        agent_id=agent_id,
        binding_id=binding_id,
        owner_user_id=owner_user_id,
    )
    try:
        created = await repository.create_from_secret_ingest(
            agent_id=agent_id,
            binding_id=binding_id,
            owner_user_id=owner_user_id,
            app_id=body.app_id,
            secret_ref=secret_ref,
        )
    except BaseException:
        await _discard_unstaged_secret(
            repository,
            secrets,
            secret_ref,
            agent_id=agent_id,
            binding_id=binding_id,
            owner_user_id=owner_user_id,
        )
        raise
    if created is None:
        await _discard_unstaged_secret(
            repository,
            secrets,
            secret_ref,
            agent_id=agent_id,
            binding_id=binding_id,
            owner_user_id=owner_user_id,
        )
        raise HTTPException(status_code=409, detail="Channel credential ingest is no longer available")
    return _safe_binding(created)


@router.get("")
async def list_agent_channels(agent_id: str, request: Request) -> list[dict[str, Any]]:
    """List the authenticated owner's redacted bindings for one Agent."""
    repository = _repository(request)
    owner_user_id = _owner_id(request)
    await _require_owned_agent(repository, agent_id, owner_user_id)
    return [_safe_binding(row) for row in await repository.list_by_agent(agent_id, owner_user_id=owner_user_id)]


@router.post("/{binding_id}/test")
async def test_agent_channel(agent_id: str, binding_id: str, request: Request) -> dict[str, Any]:
    """Test provider credentials and persist only a redacted health result."""
    repository = _repository(request)
    owner_user_id = _owner_id(request)
    await _binding_or_404(repository, agent_id, binding_id, owner_user_id)
    result = await _supervisor(request).test_binding(binding_id)
    return {"health": result.health, "detail": result.detail}


async def _lifecycle_action(agent_id: str, binding_id: str, request: Request, action: str) -> dict[str, Any]:
    """Run an owner-authorized Supervisor lifecycle operation."""
    repository = _repository(request)
    owner_user_id = _owner_id(request)
    await _binding_or_404(repository, agent_id, binding_id, owner_user_id)
    supervisor = _supervisor(request)
    try:
        await getattr(supervisor, f"{action}_binding")(binding_id)
    except ActiveAgentChannelConflictError as exc:
        raise HTTPException(status_code=409, detail="Agent already has an active Feishu binding") from exc
    except BindingCleanupPendingError as exc:
        raise HTTPException(status_code=409, detail="Channel binding deletion is pending") from exc
    except BindingNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Channel binding not found") from exc
    return _safe_binding(await _binding_or_404(repository, agent_id, binding_id, owner_user_id))


@router.post("/{binding_id}/start")
async def start_agent_channel(agent_id: str, binding_id: str, request: Request) -> dict[str, Any]:
    """Activate a binding and return only after WebSocket readiness."""
    return await _lifecycle_action(agent_id, binding_id, request, "start")


@router.post("/{binding_id}/stop")
async def stop_agent_channel(agent_id: str, binding_id: str, request: Request) -> dict[str, Any]:
    """Stop a binding connection before persisting inactive desired state."""
    return await _lifecycle_action(agent_id, binding_id, request, "stop")


@router.post("/{binding_id}/restart")
async def restart_agent_channel(agent_id: str, binding_id: str, request: Request) -> dict[str, Any]:
    """Replace a binding runtime without overlapping WebSocket connections."""
    return await _lifecycle_action(agent_id, binding_id, request, "restart")


@router.patch("/{binding_id}")
async def rotate_agent_channel_credentials(
    agent_id: str,
    binding_id: str,
    body: AgentChannelUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    """Rotate an encrypted credential bundle and restart an active binding."""
    repository = _repository(request)
    secrets = _secret_store(request)
    owner_user_id = _owner_id(request)
    current = await _binding_or_404(repository, agent_id, binding_id, owner_user_id)
    if current.get("secret_cleanup_ref"):
        try:
            await _supervisor(request).cleanup_binding_secrets(binding_id)
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Previous credential cleanup is still pending") from exc
        current = await _binding_or_404(repository, agent_id, binding_id, owner_user_id)
    app_secret = body.app_secret
    verification_token = body.verification_token
    if app_secret is None or verification_token is None:
        # The Pydantic model validator rejects this first; keep the route
        # explicitly narrowed for static typing and defensive direct calls.
        raise HTTPException(status_code=422, detail="Complete credentials are required")
    new_ref = await _put_database_owned_secret(
        repository,
        secrets,
        _secret_payload(
            app_secret=app_secret,
            verification_token=verification_token,
            encrypt_key=body.encrypt_key,
        ),
        agent_id=agent_id,
        binding_id=binding_id,
        owner_user_id=owner_user_id,
    )
    try:
        await _supervisor(request).rotate_binding_credentials(
            agent_id,
            binding_id,
            owner_user_id=owner_user_id,
            app_id=body.app_id or str(current["app_id"]),
            secret_ref=new_ref,
        )
    except AgentChannelSecretCleanupPendingError as exc:
        await _discard_unstaged_secret(repository, secrets, new_ref, agent_id=agent_id, binding_id=binding_id, owner_user_id=owner_user_id)
        raise HTTPException(status_code=409, detail="Previous credential cleanup is still pending") from exc
    except BindingCleanupPendingError as exc:
        await _discard_unstaged_secret(repository, secrets, new_ref, agent_id=agent_id, binding_id=binding_id, owner_user_id=owner_user_id)
        raise HTTPException(status_code=409, detail="Channel binding deletion is pending") from exc
    except BindingNotFoundError as exc:
        await _discard_unstaged_secret(repository, secrets, new_ref, agent_id=agent_id, binding_id=binding_id, owner_user_id=owner_user_id)
        raise HTTPException(status_code=404, detail="Channel binding not found") from exc
    except BindingStartError as exc:
        try:
            await _supervisor(request).cleanup_binding_secrets(binding_id)
        except Exception:
            logger.warning("Failed to erase rolled-back Feishu credential", extra={"binding_id": binding_id})
        raise HTTPException(status_code=502, detail="Rotated credentials failed readiness; previous credentials restored") from exc
    except BaseException:
        latest = await repository.get(agent_id, binding_id, owner_user_id=owner_user_id)
        if latest is not None and new_ref in {
            latest.get("secret_ref"),
            latest.get("secret_cleanup_ref"),
            latest.get("rotation_previous_secret_ref"),
        }:
            try:
                await _supervisor(request).cleanup_binding_secrets(binding_id)
            except Exception:
                logger.warning("Failed to erase rejected Feishu credential", extra={"binding_id": binding_id})
        else:
            await _discard_unstaged_secret(repository, secrets, new_ref, agent_id=agent_id, binding_id=binding_id, owner_user_id=owner_user_id)
        raise
    try:
        await _supervisor(request).cleanup_binding_secrets(binding_id)
    except Exception:
        logger.warning("Failed to delete superseded Feishu credential", extra={"binding_id": binding_id})
    return _safe_binding(await _binding_or_404(repository, agent_id, binding_id, owner_user_id))


@router.delete("/{binding_id}")
async def delete_agent_channel(agent_id: str, binding_id: str, request: Request) -> dict[str, bool]:
    """Stop, delete and erase one owner-scoped binding's encrypted secret."""
    repository = _repository(request)
    owner_user_id = _owner_id(request)
    await _binding_or_404(repository, agent_id, binding_id, owner_user_id)
    try:
        await _supervisor(request).delete_binding(
            agent_id,
            binding_id,
            owner_user_id=owner_user_id,
        )
    except BindingCleanupPendingError as exc:
        raise HTTPException(
            status_code=409,
            detail="Channel attachment cleanup is still pending",
        ) from exc
    except BindingNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Channel binding not found") from exc
    return {"deleted": True}
