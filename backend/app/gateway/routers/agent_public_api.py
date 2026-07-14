"""Credential-scoped public API for stable published Agents."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.gateway.deps import (
    get_checkpointer,
    get_external_conversation_repo,
    get_external_idempotency_repo,
    get_published_agent_repo,
    get_published_agent_resolver,
    get_quota_ledger,
    get_run_manager,
    get_stream_bridge,
    get_thread_store,
)
from app.gateway.external.agent_serialization import (
    sanitize_stream_payload,
    serialize_agent_conversation,
    serialize_agent_metadata,
    serialize_agent_run,
)
from app.gateway.external.service import ExternalConversationService
from app.gateway.routers.thread_runs import RunCreateRequest
from app.gateway.services import format_sse, start_run
from deerflow.persistence.external_conversation import (
    ExternalConversationExistsError,
    ExternalConversationRepository,
)
from deerflow.persistence.external_idempotency import (
    ExternalIdempotencyRepository,
    IdempotencyConflictError,
)
from deerflow.publishing.context import PublishedAgentContext
from deerflow.publishing.quota import QuotaExceededError, QuotaLedger, Reservation
from deerflow.publishing.resolver import (
    AgentNotAvailableError,
    AgentSuspendedError,
    PublishedAgentResolver,
)
from deerflow.runtime import END_SENTINEL, HEARTBEAT_SENTINEL, DisconnectMode, RunStatus

router = APIRouter(prefix="/api/v1/agents/{agent_id}", tags=["published-agent-api"])
_MAX_METADATA_BYTES = 32 * 1024
logger = logging.getLogger(__name__)


class _PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentConversationCreateRequest(_PublicModel):
    external_conversation_id: str | None = Field(default=None, min_length=1, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _metadata_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)


class AgentRunCreateRequest(_PublicModel):
    message: str = Field(min_length=1, max_length=200_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value

    @field_validator("metadata")
    @classmethod
    def _metadata_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)


def _validate_metadata(value: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must contain only JSON-compatible values") from exc
    if len(encoded) > _MAX_METADATA_BYTES:
        raise ValueError("metadata must not exceed 32 KB")
    return value


def _credential_id(request: Request) -> str:
    value = getattr(request.state, "agent_key_id", None)
    if not value:
        raise HTTPException(status_code=401, detail={"code": "invalid_agent_key"})
    return str(value)


def _owner_user_id(request: Request) -> str:
    value = getattr(request.state, "owner_user_id", None)
    if not value:
        raise HTTPException(status_code=401, detail={"code": "invalid_agent_key"})
    return str(value)


def _validate_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > 128:
        raise HTTPException(status_code=422, detail={"code": "invalid_idempotency_key"})
    return value


def _request_hash(operation: str, body: AgentRunCreateRequest) -> str:
    payload = {"operation": operation, "body": body.model_dump(mode="json")}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def _resolve_context(
    *,
    resolver: PublishedAgentResolver,
    agent_id: str,
    request: Request,
    conversation_scope: str,
    idempotency_key: str | None = None,
) -> PublishedAgentContext:
    try:
        return await resolver.resolve(
            agent_id,
            source=f"agent-api:{_credential_id(request)}",
            credential_id=_credential_id(request),
            external_actor=f"agent-key:{_credential_id(request)}",
            conversation_scope=conversation_scope,
            correlation_id=str(getattr(request.state, "request_id", "")) or "unknown",
            idempotency_key=idempotency_key,
        )
    except AgentSuspendedError as exc:
        raise HTTPException(status_code=410, detail={"code": "agent_suspended"}) from exc
    except AgentNotAvailableError as exc:
        raise HTTPException(status_code=404, detail={"code": "agent_not_found"}) from exc


async def _conversation_or_404(
    repository: ExternalConversationRepository,
    *,
    agent_id: str,
    credential_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    row = await repository.get_for_agent(
        conversation_id,
        agent_id=agent_id,
        credential_id=credential_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "conversation_not_found"})
    return row


def _run_belongs_to_scope(
    row: Any,
    *,
    agent_id: str,
    credential_id: str,
    conversation_id: str,
) -> bool:
    metadata = row.metadata if hasattr(row, "metadata") else (row.get("metadata") or {})
    return (
        metadata.get("published_agent_id") == agent_id
        and metadata.get("published_credential_id") == credential_id
        and metadata.get("published_conversation_id") == conversation_id
    )


async def _run_or_404(
    request: Request,
    run_id: str,
    *,
    agent_id: str,
    conversation_id: str,
) -> Any:
    row = await get_run_manager(request).get(run_id, user_id=_owner_user_id(request))
    if row is None or not _run_belongs_to_scope(
        row,
        agent_id=agent_id,
        credential_id=_credential_id(request),
        conversation_id=conversation_id,
    ):
        raise HTTPException(status_code=404, detail={"code": "run_not_found"})
    return row


def _internal_run_request(
    body: AgentRunCreateRequest,
    *,
    context: PublishedAgentContext,
    conversation_id: str,
    reservation_id: str,
) -> RunCreateRequest:
    metadata = {
        "published_agent": True,
        "published_agent_id": context.agent_id,
        "published_credential_id": context.credential_id,
        "published_conversation_id": conversation_id,
        "published_release_id": context.release_id,
        "published_correlation_id": context.correlation_id,
        "published_idempotency_key": context.idempotency_key,
        "published_quota_reservation_id": reservation_id,
        "external_source": "api",
        "client_metadata": body.metadata,
    }
    return RunCreateRequest(
        assistant_id="lead_agent",
        input={"messages": [{"role": "user", "content": body.message}]},
        metadata=metadata,
        stream_mode=["values", "messages-tuple", "custom"],
        on_disconnect="continue",
        multitask_strategy="reject",
    )


async def _claim_run(
    repository: ExternalIdempotencyRepository,
    *,
    request: Request,
    operation: str,
    body: AgentRunCreateRequest,
    idempotency_key: str | None,
) -> tuple[dict[str, Any] | None, bool]:
    if idempotency_key is None:
        return None, False
    try:
        return await repository.claim(
            {
                "user_id": _owner_user_id(request),
                "api_key_id": _credential_id(request),
                "idempotency_key": idempotency_key,
                "request_hash": _request_hash(operation, body),
                "expires_at": datetime.now(UTC) + timedelta(hours=24),
            }
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"}) from exc


def _quota_request_key(
    *,
    context: PublishedAgentContext,
    operation: str,
) -> str:
    identity = context.idempotency_key or context.correlation_id
    payload = f"{context.agent_id}:{context.credential_id}:{operation}:{identity}"
    return hashlib.sha256(payload.encode()).hexdigest()


async def _reserve_quota(
    *,
    ledger: QuotaLedger,
    context: PublishedAgentContext,
    body: AgentRunCreateRequest,
    operation: str,
) -> Reservation:
    quota = context.effective_quota
    if len(body.message.encode("utf-8")) > quota.max_input_bytes:
        raise HTTPException(status_code=413, detail={"code": "input_too_large"})
    try:
        return await ledger.reserve(
            context,
            request_key=_quota_request_key(context=context, operation=operation),
        )
    except QuotaExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": exc.code},
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


def _schedule_quota_settlement(
    *,
    request: Request,
    record: Any,
    reservation: Reservation,
    context: PublishedAgentContext,
    ledger: QuotaLedger,
) -> None:
    if record.task is None:
        return
    started_at = time.perf_counter()

    async def finalize() -> None:
        timed_out = False
        try:
            await asyncio.wait_for(
                asyncio.shield(record.task),
                timeout=context.effective_quota.max_run_seconds,
            )
        except TimeoutError:
            timed_out = True
            await get_run_manager(request).cancel(record.run_id)
        except asyncio.CancelledError:
            # Gateway shutdown leaves the reservation pending; expiry cleanup
            # releases it on the next reserve rather than mis-accounting it.
            if record.status != RunStatus.interrupted:
                return
        except Exception:
            pass

        if timed_out:
            terminal = "timeout"
        elif record.status == RunStatus.timeout:
            terminal = "timeout"
        elif record.status == RunStatus.success:
            terminal = "success"
        elif record.status == RunStatus.interrupted:
            terminal = "cancelled"
        else:
            terminal = "failed"
        error_class = {
            "cancelled": "CancelledError",
            "timeout": "TimeoutError",
            "failed": "RunError",
        }.get(terminal)
        usage = {
            "owner_user_id": context.owner_user_id,
            "agent_id": context.agent_id,
            "source": context.source,
            "credential_id": context.credential_id,
            "external_actor_hash": hashlib.sha256(
                context.external_actor.encode("utf-8")
            ).hexdigest(),
            "conversation_id": context.conversation_scope,
            "run_id": record.run_id,
            "model": context.model_name,
            "input_tokens": int(getattr(record, "total_input_tokens", 0) or 0),
            "output_tokens": int(getattr(record, "total_output_tokens", 0) or 0),
            "total_tokens": int(getattr(record, "total_tokens", 0) or 0),
            "latency_ms": max(0, int((time.perf_counter() - started_at) * 1000)),
            "status": terminal,
            "error_class": error_class,
            "idempotency_key": context.idempotency_key,
            "correlation_id": context.correlation_id,
        }
        await ledger.settle(
            reservation.id,
            tokens_used=usage["total_tokens"],
            status=terminal,
            run_id=record.run_id,
            usage=usage,
        )
        logger.info(
            "Published Agent run completed",
            extra={
                "agent_id": context.agent_id,
                "correlation_id": context.correlation_id,
                "release_id": context.release_id,
                "run_id": record.run_id,
                "run_status": terminal,
            },
        )

    task = asyncio.create_task(finalize())
    tasks = getattr(request.app.state, "agent_quota_tasks", None)
    if tasks is None:
        tasks = set()
        request.app.state.agent_quota_tasks = tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def _start_public_run(
    *,
    agent_id: str,
    conversation_id: str,
    body: AgentRunCreateRequest,
    request: Request,
    idempotency_key: str | None,
    operation: str,
    conversations: ExternalConversationRepository,
    idempotency: ExternalIdempotencyRepository,
    resolver: PublishedAgentResolver,
    quota_ledger: QuotaLedger,
) -> tuple[Any, bool]:
    conversation = await _conversation_or_404(
        conversations,
        agent_id=agent_id,
        credential_id=_credential_id(request),
        conversation_id=conversation_id,
    )
    if conversation["status"] != "active":
        raise HTTPException(status_code=409, detail={"code": "conversation_closed"})

    replay, claimed = await _claim_run(
        idempotency,
        request=request,
        operation=operation,
        body=body,
        idempotency_key=idempotency_key,
    )
    if replay is not None and replay.get("response_json"):
        run_id = str(replay["response_json"]["run_id"])
        return await _run_or_404(
            request,
            run_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
        ), True
    if replay is not None and not claimed:
        raise HTTPException(status_code=409, detail={"code": "idempotency_in_progress"})

    context = await _resolve_context(
        resolver=resolver,
        agent_id=agent_id,
        request=request,
        conversation_scope=conversation_id,
        idempotency_key=idempotency_key,
    )
    try:
        reservation = await _reserve_quota(
            ledger=quota_ledger,
            context=context,
            body=body,
            operation=operation,
        )
    except HTTPException:
        if claimed and idempotency_key is not None:
            await idempotency.release(
                api_key_id=_credential_id(request),
                idempotency_key=idempotency_key,
            )
        raise
    internal = _internal_run_request(
        body,
        context=context,
        conversation_id=conversation_id,
        reservation_id=reservation.id,
    )
    try:
        record = await start_run(
            internal,
            conversation["thread_id"],
            request,
            published_context=context,
        )
    except HTTPException as exc:
        await quota_ledger.release(reservation.id)
        if claimed and idempotency_key is not None:
            await idempotency.release(
                api_key_id=_credential_id(request),
                idempotency_key=idempotency_key,
            )
        if exc.status_code == 409:
            raise HTTPException(status_code=409, detail={"code": "conversation_busy"}) from exc
        raise
    except Exception:
        await quota_ledger.release(reservation.id)
        if claimed and idempotency_key is not None:
            await idempotency.release(
                api_key_id=_credential_id(request),
                idempotency_key=idempotency_key,
            )
        raise

    response = serialize_agent_run(record, conversation_id=conversation_id)
    if claimed and idempotency_key is not None:
        await idempotency.complete(
            api_key_id=_credential_id(request),
            idempotency_key=idempotency_key,
            run_id=record.run_id,
            response_status=202,
            response_json=response,
        )
    _schedule_quota_settlement(
        request=request,
        record=record,
        reservation=reservation,
        context=context,
        ledger=quota_ledger,
    )
    request.state.external_audit_resource_type = "run"
    request.state.external_audit_resource_id = record.run_id
    logger.info(
        "Published Agent run started",
        extra={
            "agent_id": context.agent_id,
            "correlation_id": context.correlation_id,
            "release_id": context.release_id,
            "run_id": record.run_id,
        },
    )
    return record, False


async def _public_sse_consumer(
    *,
    request: Request,
    record: Any,
    conversation_id: str,
):
    bridge = get_stream_bridge(request)
    manager = get_run_manager(request)
    last_event_id = request.headers.get("Last-Event-ID")
    try:
        async for entry in bridge.subscribe(record.run_id, last_event_id=last_event_id):
            if await request.is_disconnected():
                break
            if entry is HEARTBEAT_SENTINEL:
                yield ": heartbeat\n\n"
                continue
            if entry is END_SENTINEL:
                yield format_sse("end", None, event_id=entry.id or None)
                return
            if entry.event == "metadata":
                data = {"run_id": record.run_id, "conversation_id": conversation_id}
            else:
                data = sanitize_stream_payload(entry.data)
            yield format_sse(entry.event, data, event_id=entry.id or None)
    finally:
        if record.status in (RunStatus.pending, RunStatus.running):
            if record.on_disconnect == DisconnectMode.cancel:
                await manager.cancel(record.run_id)


@router.get("")
async def get_agent_metadata(
    agent_id: str,
    request: Request,
    resolver: PublishedAgentResolver = Depends(get_published_agent_resolver),
    agents=Depends(get_published_agent_repo),
) -> dict[str, Any]:
    await _resolve_context(
        resolver=resolver,
        agent_id=agent_id,
        request=request,
        conversation_scope="metadata",
    )
    agent = await agents.get(agent_id, owner_user_id=_owner_user_id(request))
    if agent is None:
        raise HTTPException(status_code=404, detail={"code": "agent_not_found"})
    return serialize_agent_metadata(agent)


@router.post("/conversations", status_code=201)
async def create_agent_conversation(
    agent_id: str,
    body: AgentConversationCreateRequest,
    request: Request,
    repository: ExternalConversationRepository = Depends(get_external_conversation_repo),
    resolver: PublishedAgentResolver = Depends(get_published_agent_resolver),
) -> dict[str, Any]:
    await _resolve_context(
        resolver=resolver,
        agent_id=agent_id,
        request=request,
        conversation_scope="new",
    )
    service = ExternalConversationService(
        repository,
        thread_store=get_thread_store(request),
        checkpointer=get_checkpointer(request),
    )
    try:
        row = await service.create(
            user_id=_owner_user_id(request),
            source="api",
            external_conversation_id=body.external_conversation_id,
            agent_id=agent_id,
            default_skill_name=None,
            metadata=body.metadata,
            credential_id=_credential_id(request),
            runtime_assistant_id="lead_agent",
        )
    except ExternalConversationExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "external_conversation_exists", "conversation_id": exc.conversation_id},
        ) from exc
    return serialize_agent_conversation(row)


@router.get("/conversations/{conversation_id}")
async def get_agent_conversation(
    agent_id: str,
    conversation_id: str,
    request: Request,
    repository: ExternalConversationRepository = Depends(get_external_conversation_repo),
    resolver: PublishedAgentResolver = Depends(get_published_agent_resolver),
) -> dict[str, Any]:
    await _resolve_context(
        resolver=resolver,
        agent_id=agent_id,
        request=request,
        conversation_scope=conversation_id,
    )
    row = await _conversation_or_404(
        repository,
        agent_id=agent_id,
        credential_id=_credential_id(request),
        conversation_id=conversation_id,
    )
    return serialize_agent_conversation(row)


@router.post("/conversations/{conversation_id}/runs", status_code=202)
async def create_agent_run(
    agent_id: str,
    conversation_id: str,
    body: AgentRunCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    conversations: ExternalConversationRepository = Depends(get_external_conversation_repo),
    idempotency: ExternalIdempotencyRepository = Depends(get_external_idempotency_repo),
    resolver: PublishedAgentResolver = Depends(get_published_agent_resolver),
    quota_ledger: QuotaLedger = Depends(get_quota_ledger),
) -> dict[str, Any]:
    idempotency_key = _validate_idempotency_key(idempotency_key)
    record, _replayed = await _start_public_run(
        agent_id=agent_id,
        conversation_id=conversation_id,
        body=body,
        request=request,
        idempotency_key=idempotency_key,
        operation=f"create_run:{agent_id}:{conversation_id}",
        conversations=conversations,
        idempotency=idempotency,
        resolver=resolver,
        quota_ledger=quota_ledger,
    )
    return serialize_agent_run(record, conversation_id=conversation_id)


@router.post("/conversations/{conversation_id}/runs/wait")
async def wait_agent_run(
    agent_id: str,
    conversation_id: str,
    body: AgentRunCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    conversations: ExternalConversationRepository = Depends(get_external_conversation_repo),
    idempotency: ExternalIdempotencyRepository = Depends(get_external_idempotency_repo),
    resolver: PublishedAgentResolver = Depends(get_published_agent_resolver),
    quota_ledger: QuotaLedger = Depends(get_quota_ledger),
) -> dict[str, Any]:
    idempotency_key = _validate_idempotency_key(idempotency_key)
    record, replayed = await _start_public_run(
        agent_id=agent_id,
        conversation_id=conversation_id,
        body=body,
        request=request,
        idempotency_key=idempotency_key,
        operation=f"wait_run:{agent_id}:{conversation_id}",
        conversations=conversations,
        idempotency=idempotency,
        resolver=resolver,
        quota_ledger=quota_ledger,
    )
    if not replayed and record.task is not None:
        try:
            await record.task
        except asyncio.CancelledError:
            pass
    return serialize_agent_run(record, conversation_id=conversation_id)


@router.post("/conversations/{conversation_id}/runs/stream")
async def stream_agent_run(
    agent_id: str,
    conversation_id: str,
    body: AgentRunCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    conversations: ExternalConversationRepository = Depends(get_external_conversation_repo),
    idempotency: ExternalIdempotencyRepository = Depends(get_external_idempotency_repo),
    resolver: PublishedAgentResolver = Depends(get_published_agent_resolver),
    quota_ledger: QuotaLedger = Depends(get_quota_ledger),
) -> StreamingResponse:
    idempotency_key = _validate_idempotency_key(idempotency_key)
    record, _replayed = await _start_public_run(
        agent_id=agent_id,
        conversation_id=conversation_id,
        body=body,
        request=request,
        idempotency_key=idempotency_key,
        operation=f"stream_run:{agent_id}:{conversation_id}",
        conversations=conversations,
        idempotency=idempotency,
        resolver=resolver,
        quota_ledger=quota_ledger,
    )
    return StreamingResponse(
        _public_sse_consumer(
            request=request,
            record=record,
            conversation_id=conversation_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Location": (
                f"/api/v1/agents/{agent_id}/conversations/{conversation_id}/runs/{record.run_id}"
            ),
        },
    )


@router.get("/conversations/{conversation_id}/runs/{run_id}")
async def get_agent_run(
    agent_id: str,
    conversation_id: str,
    run_id: str,
    request: Request,
    conversations: ExternalConversationRepository = Depends(get_external_conversation_repo),
    resolver: PublishedAgentResolver = Depends(get_published_agent_resolver),
) -> dict[str, Any]:
    await _resolve_context(
        resolver=resolver,
        agent_id=agent_id,
        request=request,
        conversation_scope=conversation_id,
    )
    await _conversation_or_404(
        conversations,
        agent_id=agent_id,
        credential_id=_credential_id(request),
        conversation_id=conversation_id,
    )
    row = await _run_or_404(
        request,
        run_id,
        agent_id=agent_id,
        conversation_id=conversation_id,
    )
    return serialize_agent_run(row, conversation_id=conversation_id)


@router.post("/conversations/{conversation_id}/runs/{run_id}/cancel")
async def cancel_agent_run(
    agent_id: str,
    conversation_id: str,
    run_id: str,
    request: Request,
    conversations: ExternalConversationRepository = Depends(get_external_conversation_repo),
    resolver: PublishedAgentResolver = Depends(get_published_agent_resolver),
) -> dict[str, Any]:
    await _resolve_context(
        resolver=resolver,
        agent_id=agent_id,
        request=request,
        conversation_scope=conversation_id,
    )
    await _conversation_or_404(
        conversations,
        agent_id=agent_id,
        credential_id=_credential_id(request),
        conversation_id=conversation_id,
    )
    row = await _run_or_404(
        request,
        run_id,
        agent_id=agent_id,
        conversation_id=conversation_id,
    )
    if row.status in (RunStatus.pending, RunStatus.running, RunStatus.interrupted):
        await get_run_manager(request).cancel(run_id)
    updated = await _run_or_404(
        request,
        run_id,
        agent_id=agent_id,
        conversation_id=conversation_id,
    )
    return serialize_agent_run(updated, conversation_id=conversation_id)
