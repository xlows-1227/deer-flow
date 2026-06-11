"""Stable External API V1 facade."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.gateway.deps import (
    get_checkpointer,
    get_config,
    get_external_conversation_repo,
    get_external_idempotency_repo,
    get_run_manager,
    get_run_store,
    get_thread_store,
)
from app.gateway.external.config import get_external_api_config
from app.gateway.external.errors import ExternalAPIError
from app.gateway.external.models import (
    ExternalConversationCreateRequest,
    ExternalConversationResponse,
    ExternalRunCreateRequest,
    ExternalRunResponse,
    ExternalSkillsResponse,
    ExternalSkillSummary,
)
from app.gateway.external.service import ExternalConversationService
from app.gateway.external.skill_policy import (
    available_external_skills,
    require_external_agent,
    require_external_skill,
)
from app.gateway.routers.thread_runs import RunCreateRequest
from app.gateway.services import start_run
from deerflow.persistence.external_conversation import (
    ExternalConversationExistsError,
    ExternalConversationRepository,
)
from deerflow.persistence.external_idempotency import (
    ExternalIdempotencyRepository,
    IdempotencyConflictError,
)
from deerflow.runtime import RunStatus

router = APIRouter(prefix="/api/v1/external", tags=["external-api"])


def _require_scope(request: Request, scope: str) -> None:
    if scope not in getattr(request.state, "external_scopes", []):
        raise HTTPException(status_code=403, detail={"code": "insufficient_scope"})


def _user_id(request: Request) -> str:
    return str(request.state.user.id)


def _validate_idempotency_key(value: str | None) -> str | None:
    if value is not None and (not value.strip() or len(value) > 128):
        raise HTTPException(status_code=422, detail={"code": "invalid_idempotency_key"})
    return value.strip() if value is not None else None


def _request_hash(operation: str, body: dict[str, Any]) -> str:
    payload = {"operation": operation, "body": body}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _run_context(*, mode: str, skill_name: str | None, allowed_skills: list[str]) -> dict[str, Any]:
    resolved_mode = "pro" if mode == "standard" else mode
    reasoning_effort = {
        "thinking": "low",
        "pro": "medium",
        "ultra": "high",
    }.get(resolved_mode)
    return {
        "skill_name": skill_name,
        "external_allowed_skills": list(allowed_skills),
        "mode": resolved_mode,
        "thinking_enabled": resolved_mode != "flash",
        "is_plan_mode": resolved_mode in ("pro", "ultra"),
        "subagent_enabled": resolved_mode == "ultra",
        "reasoning_effort": reasoning_effort,
    }


def _set_audit_context(
    request: Request,
    *,
    resource_type: str,
    resource_id: str,
    skill_name: str | None = None,
) -> None:
    request.state.external_audit_resource_type = resource_type
    request.state.external_audit_resource_id = resource_id
    request.state.external_audit_skill_name = skill_name


def _conversation_response(row: dict[str, Any], *, request_id: str | None = None) -> ExternalConversationResponse:
    return ExternalConversationResponse(
        request_id=request_id,
        conversation_id=row["conversation_id"],
        status=row["status"],
        agent=row["agent_id"],
        default_skill=row.get("default_skill_name"),
        source=row["source"],
        external_conversation_id=row.get("external_conversation_id"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _run_response(row: Any, *, conversation_id: str | None = None, request_id: str | None = None) -> ExternalRunResponse:
    metadata = row.metadata if hasattr(row, "metadata") else row.get("metadata", {})
    status = row.status.value if hasattr(row, "status") else row.get("status", "error")
    from app.gateway.external.status import to_external_run_status

    external_status = to_external_run_status(status)
    return ExternalRunResponse(
        request_id=request_id,
        run_id=row.run_id if hasattr(row, "run_id") else row["run_id"],
        conversation_id=conversation_id or metadata.get("external_conversation_id", ""),
        skill=metadata.get("skill_name"),
        status=external_status,
        answer=getattr(row, "last_ai_message", None) if hasattr(row, "last_ai_message") else row.get("last_ai_message"),
        error="The run failed." if external_status == "failed" else None,
        created_at=getattr(row, "created_at", None) if hasattr(row, "created_at") else row.get("created_at"),
        updated_at=getattr(row, "updated_at", None) if hasattr(row, "updated_at") else row.get("updated_at"),
    )


@router.get("/skills", response_model=ExternalSkillsResponse)
async def list_external_skills(request: Request, config=Depends(get_config)) -> ExternalSkillsResponse:
    _require_scope(request, "external:skills:read")
    skills = available_external_skills(
        app_config=config,
        allowed_skills=getattr(request.state, "allowed_skills", []),
    )
    return ExternalSkillsResponse(
        request_id=getattr(request.state, "request_id", None),
        skills=[
            ExternalSkillSummary(
                name=skill.name,
                description=skill.description,
                display_name=skill.display_name,
                description_zh=skill.description_zh,
            )
            for skill in skills
        ],
    )


@router.post("/conversations", response_model=ExternalConversationResponse, status_code=201)
async def create_external_conversation(
    body: ExternalConversationCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    repository: ExternalConversationRepository = Depends(get_external_conversation_repo),
    idempotency: ExternalIdempotencyRepository = Depends(get_external_idempotency_repo),
    config=Depends(get_config),
) -> ExternalConversationResponse:
    _require_scope(request, "external:conversations:create")
    idempotency_key = _validate_idempotency_key(idempotency_key)
    try:
        require_external_agent(body.agent)
    except ExternalAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_response()["error"]) from exc
    if body.default_skill:
        try:
            require_external_skill(
                app_config=config,
                allowed_skills=request.state.allowed_skills,
                agent_id=body.agent,
                skill_name=body.default_skill,
            )
        except ExternalAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.to_response()["error"]) from exc

    request_hash = _request_hash("create_conversation", body.model_dump(mode="json"))
    claimed = False
    if idempotency_key:
        try:
            if hasattr(idempotency, "claim"):
                replay, claimed = await idempotency.claim(
                    {
                        "user_id": _user_id(request),
                        "api_key_id": request.state.api_key_id,
                        "idempotency_key": idempotency_key,
                        "request_hash": request_hash,
                        "expires_at": datetime.now(UTC) + timedelta(hours=24),
                    }
                )
            else:
                replay = await idempotency.get(
                    api_key_id=request.state.api_key_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"}) from exc
        if replay and replay.get("response_json"):
            response = ExternalConversationResponse.model_validate(replay["response_json"])
            _set_audit_context(request, resource_type="conversation", resource_id=response.conversation_id)
            return response.model_copy(update={"request_id": getattr(request.state, "request_id", None)})
        if replay and not claimed:
            raise HTTPException(status_code=409, detail={"code": "idempotency_in_progress"})

    service = ExternalConversationService(
        repository,
        thread_store=get_thread_store(request),
        checkpointer=get_checkpointer(request),
    )
    try:
        row = await service.create(
            user_id=_user_id(request),
            source=body.source,
            external_conversation_id=body.external_conversation_id,
            agent_id=body.agent,
            default_skill_name=body.default_skill,
            metadata=body.metadata,
        )
    except ExternalConversationExistsError as exc:
        if claimed:
            await idempotency.release(api_key_id=request.state.api_key_id, idempotency_key=idempotency_key)
        raise HTTPException(
            status_code=409,
            detail={"code": "external_conversation_exists", "conversation_id": exc.conversation_id},
        ) from exc
    except Exception:
        if claimed:
            await idempotency.release(api_key_id=request.state.api_key_id, idempotency_key=idempotency_key)
        raise
    _set_audit_context(request, resource_type="conversation", resource_id=row["conversation_id"])
    response = _conversation_response(row, request_id=getattr(request.state, "request_id", None))
    if idempotency_key:
        if claimed:
            await idempotency.complete(
                api_key_id=request.state.api_key_id,
                idempotency_key=idempotency_key,
                run_id=None,
                response_status=201,
                response_json=response.model_dump(mode="json"),
            )
        else:
            await idempotency.put(
                {
                    "user_id": _user_id(request),
                    "api_key_id": request.state.api_key_id,
                    "idempotency_key": idempotency_key,
                    "request_hash": request_hash,
                    "response_status": 201,
                    "response_json": response.model_dump(mode="json"),
                    "expires_at": datetime.now(UTC) + timedelta(hours=24),
                }
            )
    return response


@router.get("/conversations/{conversation_id}", response_model=ExternalConversationResponse)
async def get_external_conversation(
    conversation_id: str,
    request: Request,
    repository: ExternalConversationRepository = Depends(get_external_conversation_repo),
) -> ExternalConversationResponse:
    _require_scope(request, "external:conversations:read")
    row = await repository.get(conversation_id, user_id=_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "conversation_not_found"})
    _set_audit_context(request, resource_type="conversation", resource_id=conversation_id)
    return _conversation_response(row, request_id=getattr(request.state, "request_id", None))


@router.post("/conversations/{conversation_id}/runs", response_model=ExternalRunResponse, status_code=202)
async def create_external_run(
    conversation_id: str,
    body: ExternalRunCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    conversations: ExternalConversationRepository = Depends(get_external_conversation_repo),
    idempotency: ExternalIdempotencyRepository = Depends(get_external_idempotency_repo),
    config=Depends(get_config),
) -> ExternalRunResponse:
    _require_scope(request, "external:runs:create")
    idempotency_key = _validate_idempotency_key(idempotency_key)
    conversation = await conversations.get(conversation_id, user_id=_user_id(request))
    if conversation is None:
        raise HTTPException(status_code=404, detail={"code": "conversation_not_found"})
    if conversation["status"] != "active":
        raise HTTPException(status_code=409, detail={"code": "conversation_closed"})

    final_skill = body.skill or conversation.get("default_skill_name")
    if final_skill:
        if body.mode == "flash":
            raise HTTPException(status_code=422, detail={"code": "flash_not_available_with_skill"})
        try:
            require_external_skill(
                app_config=config,
                allowed_skills=request.state.allowed_skills,
                agent_id=conversation["agent_id"],
                skill_name=final_skill,
            )
        except ExternalAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.to_response()["error"]) from exc

    request_hash = _request_hash(f"create_run:{conversation_id}", body.model_dump(mode="json"))
    claimed = False
    if idempotency_key:
        try:
            if hasattr(idempotency, "claim"):
                replay, claimed = await idempotency.claim(
                    {
                        "user_id": _user_id(request),
                        "api_key_id": request.state.api_key_id,
                        "idempotency_key": idempotency_key,
                        "request_hash": request_hash,
                        "expires_at": datetime.now(UTC) + timedelta(hours=24),
                    }
                )
            else:
                replay = await idempotency.get(
                    api_key_id=request.state.api_key_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"}) from exc
        if replay and replay.get("response_json"):
            response = ExternalRunResponse.model_validate(replay["response_json"])
            _set_audit_context(
                request,
                resource_type="run",
                resource_id=response.run_id,
                skill_name=response.skill,
            )
            return response.model_copy(update={"request_id": getattr(request.state, "request_id", None)})
        if replay and not claimed:
            raise HTTPException(status_code=409, detail={"code": "idempotency_in_progress"})

    run_store = get_run_store(request)
    if await run_store.count_inflight_by_user(_user_id(request)) >= get_external_api_config().active_run_limit_per_user:
        if claimed:
            await idempotency.release(api_key_id=request.state.api_key_id, idempotency_key=idempotency_key)
        raise HTTPException(status_code=429, detail={"code": "concurrency_limit_exceeded"})

    metadata = {
        "external_api": True,
        "external_conversation_id": conversation_id,
        "external_api_key_id": request.state.api_key_id,
        "external_request_id": getattr(request.state, "request_id", None),
        "skill_name": final_skill,
        "external_source": conversation["source"],
        "client_metadata": body.metadata,
    }
    internal = RunCreateRequest(
        assistant_id=conversation["agent_id"],
        input={"messages": [{"role": "user", "content": body.message}]},
        metadata=metadata,
        context=_run_context(
            mode=body.mode,
            skill_name=final_skill,
            allowed_skills=request.state.allowed_skills,
        ),
        stream_mode=["values", "messages-tuple", "custom"],
        on_disconnect="continue",
        multitask_strategy="reject",
    )
    try:
        record = await start_run(internal, conversation["thread_id"], request)
    except HTTPException as exc:
        if claimed:
            await idempotency.release(api_key_id=request.state.api_key_id, idempotency_key=idempotency_key)
        if exc.status_code == 409:
            raise HTTPException(status_code=409, detail={"code": "conversation_busy"}) from exc
        raise
    except Exception:
        if claimed:
            await idempotency.release(api_key_id=request.state.api_key_id, idempotency_key=idempotency_key)
        raise
    _set_audit_context(request, resource_type="run", resource_id=record.run_id, skill_name=final_skill)
    response = _run_response(
        record,
        conversation_id=conversation_id,
        request_id=getattr(request.state, "request_id", None),
    )
    if idempotency_key:
        if claimed:
            await idempotency.complete(
                api_key_id=request.state.api_key_id,
                idempotency_key=idempotency_key,
                run_id=response.run_id,
                response_status=202,
                response_json=response.model_dump(mode="json"),
            )
        else:
            await idempotency.put(
                {
                    "user_id": _user_id(request),
                    "api_key_id": request.state.api_key_id,
                    "idempotency_key": idempotency_key,
                    "request_hash": request_hash,
                    "run_id": response.run_id,
                    "response_status": 202,
                    "response_json": response.model_dump(mode="json"),
                    "expires_at": datetime.now(UTC) + timedelta(hours=24),
                }
            )
    return response


@router.get("/runs/{run_id}", response_model=ExternalRunResponse)
async def get_external_run(run_id: str, request: Request) -> ExternalRunResponse:
    _require_scope(request, "external:runs:read")
    row = await get_run_store(request).get(run_id, user_id=_user_id(request))
    if row is None or not (row.get("metadata") or {}).get("external_api"):
        raise HTTPException(status_code=404, detail={"code": "run_not_found"})
    _set_audit_context(
        request,
        resource_type="run",
        resource_id=run_id,
        skill_name=(row.get("metadata") or {}).get("skill_name"),
    )
    return _run_response(row, request_id=getattr(request.state, "request_id", None))


@router.post("/runs/{run_id}/cancel", response_model=ExternalRunResponse)
async def cancel_external_run(run_id: str, request: Request) -> ExternalRunResponse:
    _require_scope(request, "external:runs:cancel")
    row = await get_run_store(request).get(run_id, user_id=_user_id(request))
    if row is None or not (row.get("metadata") or {}).get("external_api"):
        raise HTTPException(status_code=404, detail={"code": "run_not_found"})
    _set_audit_context(
        request,
        resource_type="run",
        resource_id=run_id,
        skill_name=(row.get("metadata") or {}).get("skill_name"),
    )
    manager = get_run_manager(request)
    record = await manager.get(run_id, user_id=_user_id(request))
    if record is not None and record.status in (RunStatus.pending, RunStatus.running, RunStatus.interrupted):
        await manager.cancel(run_id)
    updated = await get_run_store(request).get(run_id, user_id=_user_id(request))
    return _run_response(updated or row, request_id=getattr(request.state, "request_id", None))
