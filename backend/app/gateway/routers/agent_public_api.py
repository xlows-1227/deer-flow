"""Credential-scoped public API for stable published Agents."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Union
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.gateway.deps import (
    get_checkpointer,
    get_config,
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
    serialize_agent_capabilities,
    serialize_agent_conversation,
    serialize_agent_metadata,
    serialize_agent_run,
)
from app.gateway.external.service import ExternalConversationService
from app.gateway.routers.thread_runs import RunCreateRequest
from app.gateway.services import format_sse, start_run
from deerflow.config.app_config import AppConfig
from deerflow.persistence.external_conversation import (
    ExternalConversationExistsError,
    ExternalConversationRepository,
)
from deerflow.persistence.external_idempotency import (
    ExternalIdempotencyRepository,
    IdempotencyConflictError,
)
from deerflow.persistence.published_agent import PublishedAgentRepository
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
_SETTLEMENT_MAX_ATTEMPTS = 3
_SETTLEMENT_RETRY_DELAY_SECONDS = 0.05


class _PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentConversationCreateRequest(_PublicModel):
    """Public fields accepted when creating a credential-scoped conversation."""

    external_conversation_id: str | None = Field(default=None, min_length=1, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _metadata_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)


class _PublicModel(BaseModel):
    """Common Pydantic settings for public request/response models."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        use_attribute_docstrings=True,
        json_encoders={bytes: lambda v: v.decode("utf-8")},
    )


# ---------------------------------------------------------------------------
# Multimodal content parts (attachments via public API)
# ---------------------------------------------------------------------------


class TextContentPart(BaseModel):
    """A plain text part within a multimodal message."""

    type: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=200_000)


class _ImageUrlNested(BaseModel):
    url: str = Field(
        min_length=1,
        max_length=5_000,
        description=(
            "Either a publicly reachable HTTP(S) URL, or a RFC 2397 data URI such as "
            "``data:image/png;base64,<base64-bytes>``."
        ),
    )
    detail: Literal["auto", "low", "high"] = Field(default="auto")


class ImageUrlContentPart(BaseModel):
    """A single image part within a multimodal message (remote URL or data URI)."""

    type: Literal["image_url"] = "image_url"
    image_url: _ImageUrlNested


class FilePathContentPart(BaseModel):
    """Reference to a file already uploaded via ``POST /api/threads/{thread_id}/uploads``.

    Use this shape when you first upload files through the internal uploads
    endpoint and receive a server-side ``path`` back.  The referenced path
    MUST belong to the thread that backs this agent conversation, otherwise
    the agent runtime will reject it as unreadable.
    """

    type: Literal["file_path"] = "file_path"
    path: str = Field(min_length=1, max_length=1_000)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    content_type: str | None = Field(default=None, min_length=1, max_length=200)


ContentPart = Annotated[
    Union[TextContentPart, ImageUrlContentPart, FilePathContentPart],
    Field(discriminator="type"),
]

_MAX_MESSAGE_PARTS = 32
_MAX_MESSAGE_BYTES = 4_000_000  # 4 MB — covers images as data URIs or long text+image payloads


def _content_part_sizes(part: Any) -> int:
    """Upper-bound byte size estimate for a single multimodal part."""
    if isinstance(part, TextContentPart):
        return len(part.text.encode("utf-8"))
    if isinstance(part, ImageUrlContentPart):
        return len(part.image_url.url.encode("utf-8"))
    if isinstance(part, FilePathContentPart):
        return len((part.path or "").encode("utf-8")) + len((part.name or "").encode("utf-8"))
    # Fallback — serialise to JSON to guarantee boundedness
    try:
        return len(json.dumps(part, ensure_ascii=False, separators=(",", ":")).encode())
    except Exception:  # noqa: BLE001
        return 8192


class AgentRunCreateRequest(_PublicModel):
    """Public fields accepted when starting a published-Agent run.

    ``message`` accepts either:

    * a plain ``string`` (original behaviour), OR
    * a **list of ContentPart objects** (multimodal) combining ``text``,
      ``image_url`` (public URL or data:…/base64) and ``file_path``
      (server-side uploaded file references) into a single user turn.

    Multimodal example body::

        {
          "message": [
            {"type": "text", "text": "请描述这张图片的内容"},
            {
              "type": "image_url",
              "image_url": {
                "url": "https://example.com/photo.jpg",
                "detail": "auto"
              }
            }
          ]
        }
    """

    message: Union[str, list[ContentPart]] = Field(
        min_length=1,
        max_length=_MAX_MESSAGE_PARTS,
        description="Plain text, or an ordered list of multimodal content parts.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_json_list(cls, value: Any) -> Any:
        # Some form/multipart legacy callers may send JSON-encoded message as
        # a string; decode it early so the Union discriminator can dispatch.
        if isinstance(value, dict) and isinstance(value.get("message"), str):
            raw = value["message"].strip()
            if raw.startswith("[") and raw.endswith("]"):
                try:
                    value = {**value, "message": json.loads(raw)}
                except Exception:  # noqa: BLE001
                    pass
        return value

    @field_validator("message")
    @classmethod
    def _message_not_blank_and_bounded(cls, value: Any) -> Any:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("message must not be blank")
            if len(value.encode("utf-8")) > _MAX_MESSAGE_BYTES:
                raise ValueError(f"message exceeds {_MAX_MESSAGE_BYTES} bytes")
            return value
        if isinstance(value, list):
            if not value:
                raise ValueError("message parts list must not be empty")
            if len(value) > _MAX_MESSAGE_PARTS:
                raise ValueError(f"message has more than {_MAX_MESSAGE_PARTS} parts")
            total_bytes = 0
            has_text = False
            for p in value:
                if isinstance(p, TextContentPart):
                    if not p.text.strip():
                        raise ValueError("message contains a blank text part")
                    has_text = True
                total_bytes += _content_part_sizes(p)
                if total_bytes > _MAX_MESSAGE_BYTES:
                    raise ValueError(f"message exceeds {_MAX_MESSAGE_BYTES} bytes")
            # If every part is image-only (no text) → keep to the same strict
            # behaviour the Web UI has always enforced at submission time:
            # pure-image inputs are rejected up-front so the caller doesn't
            # waste a turn waiting for the model to throw "text content is
            # empty".
            if not has_text:
                raise ValueError(
                    "message must include at least one non-blank text part when sending attachments"
                )
            return value
        raise ValueError("message must be a string or a list of content parts")

    def message_as_graph_input(self) -> Any:
        """Render ``message`` into the graph ``input.messages[].content`` shape.

        * scalar strings → returned as-is (legacy path)
        * part lists → normalised to LangChain-style content arrays where a
          single trailing text part may still collapse to scalar; image_url /
          file_path parts are preserved as dicts so multimodal code paths in
          the worker recognise them correctly.
        """
        if isinstance(self.message, str):
            return self.message
        parts: list[dict[str, Any]] = []
        scalar_text_only: TextContentPart | None = None
        for p in self.message:
            if isinstance(p, TextContentPart):
                parts.append({"type": "text", "text": p.text})
                scalar_text_only = p if len(self.message) == 1 else None
            elif isinstance(p, ImageUrlContentPart):
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": p.image_url.url,
                            **(
                                {"detail": p.image_url.detail}
                                if p.image_url.detail != "auto"
                                else {}
                            ),
                        },
                    }
                )
            elif isinstance(p, FilePathContentPart):
                entry: dict[str, Any] = {"type": "file_path", "path": p.path}
                if p.name:
                    entry["name"] = p.name
                if p.content_type:
                    entry["content_type"] = p.content_type
                parts.append(entry)
        # Scalar collapse mirrors what the worker normaliser expects for
        # pure-text turns so we don't accidentally force multimodal decoding
        # on simple strings.
        if scalar_text_only is not None:
            return scalar_text_only.text
        return parts

    def message_utf8_bytes(self) -> int:
        """Quoted size for quota accounting (same upper bound as validator)."""
        if isinstance(self.message, str):
            return len(self.message.encode("utf-8"))
        total = 0
        for p in self.message:
            total += _content_part_sizes(p)
        return total

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


def _conversation_source(request: Request) -> str:
    """Encode credential scope into the legacy conversation mapping key."""
    return f"agent-api:{_credential_id(request)}"


def _owner_user_id(request: Request) -> str:
    value = getattr(request.state, "owner_user_id", None)
    if not value:
        raise HTTPException(status_code=401, detail={"code": "invalid_agent_key"})
    return str(value)


@dataclass(frozen=True)
class _PublishedRequestScope:
    owner_user_id: str
    agent_id: str
    credential_id: str
    conversation_id: str


def _request_scope(request: Request, *, agent_id: str, conversation_id: str) -> _PublishedRequestScope:
    return _PublishedRequestScope(
        owner_user_id=_owner_user_id(request),
        agent_id=agent_id,
        credential_id=_credential_id(request),
        conversation_id=conversation_id,
    )


def _validate_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > 128:
        raise HTTPException(status_code=422, detail={"code": "invalid_idempotency_key"})
    return value


def _request_hash(operation: str, body: AgentRunCreateRequest) -> str:
    payload = {"operation": operation, "body": body.model_dump(mode="json")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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
            source="api",
            credential_id=_credential_id(request),
            external_actor=f"agent-key:{_credential_id(request)}",
            conversation_scope=conversation_scope,
            correlation_id=str(getattr(request.state, "request_id", "")) or "unknown",
            idempotency_key=idempotency_key,
        )
    except AgentSuspendedError as exc:
        raise HTTPException(status_code=410, detail={"code": "agent_suspended"}) from exc
    except AgentNotAvailableError as exc:
        message = str(exc)
        detail: dict[str, str] = {"code": "agent_not_found"}
        # If the error contains actionable detail, include it in the response
        if "no model configured" in message.lower() or "model" in message.lower():
            detail["reason"] = message
        raise HTTPException(status_code=404, detail=detail) from exc


async def _conversation_or_404(
    repository: ExternalConversationRepository,
    *,
    scope: _PublishedRequestScope,
) -> dict[str, Any]:
    row = await repository.get_for_agent(
        scope.conversation_id,
        owner_user_id=scope.owner_user_id,
        agent_id=scope.agent_id,
        credential_id=scope.credential_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "conversation_not_found"})
    return row


def _run_belongs_to_scope(
    row: Any,
    *,
    scope: _PublishedRequestScope,
) -> bool:
    metadata = row.metadata if hasattr(row, "metadata") else (row.get("metadata") or {})
    return metadata.get("published_agent_id") == scope.agent_id and metadata.get("published_credential_id") == scope.credential_id and metadata.get("published_conversation_id") == scope.conversation_id


async def _run_or_404(
    request: Request,
    run_id: str,
    *,
    scope: _PublishedRequestScope,
) -> Any:
    row = await get_run_manager(request).get(run_id, user_id=scope.owner_user_id)
    if row is None or not _run_belongs_to_scope(
        row,
        scope=scope,
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
        "published_release_id": context.release_id,
        "published_credential_id": context.credential_id,
        "published_conversation_id": conversation_id,
        "published_correlation_id": context.correlation_id,
        "published_idempotency_key": context.idempotency_key,
        "published_quota_reservation_id": reservation_id,
        "external_source": "api",
        "client_metadata": body.metadata,
        **_settlement_metadata(context),
    }
    return RunCreateRequest(
        assistant_id="lead_agent",
        input={"messages": [{"role": "user", "content": body.message_as_graph_input()}]},
        metadata=metadata,
        stream_mode=["values", "messages-tuple", "custom"],
        on_disconnect="continue",
        multitask_strategy="reject",
    )


def _settlement_metadata(context: PublishedAgentContext) -> dict[str, Any]:
    """Persist immutable, non-secret usage fields with the durable Run."""
    return {
        "published_agent_id": context.agent_id,
        "published_release_id": context.release_id,
        "published_credential_id": context.credential_id,
        "published_conversation_id": context.conversation_scope,
        "published_correlation_id": context.correlation_id,
        "published_idempotency_key": context.idempotency_key,
        "published_source": context.source,
        "published_external_actor_hash": hashlib.sha256(context.external_actor.encode("utf-8")).hexdigest(),
        "published_model_name": context.model_name,
        "published_settlement_started_at": datetime.now(UTC).isoformat(),
    }


def _terminal_status(record: Any) -> str | None:
    if record.status == RunStatus.timeout:
        return "timeout"
    if record.status == RunStatus.success:
        return "success"
    if record.status == RunStatus.interrupted:
        return "cancelled"
    if record.status == RunStatus.error:
        return "failed"
    return None


def _usage_from_record(
    record: Any,
    *,
    owner_user_id: str,
    terminal: str,
    latency_ms: int,
    context: PublishedAgentContext | None = None,
) -> dict[str, Any]:
    metadata = getattr(record, "metadata", {}) or {}
    if context is None:
        agent_id = str(metadata["published_agent_id"])
        source = str(metadata["published_source"])
        credential_id = str(metadata["published_credential_id"])
        actor_hash = str(metadata["published_external_actor_hash"])
        conversation_id = str(metadata["published_conversation_id"])
        model_name = str(metadata["published_model_name"])
        idempotency_key = metadata.get("published_idempotency_key")
        correlation_id = str(metadata["published_correlation_id"])
        release_id = str(metadata["published_release_id"]) if metadata.get("published_release_id") else None
    else:
        agent_id = context.agent_id
        source = context.source
        credential_id = context.credential_id
        actor_hash = hashlib.sha256(context.external_actor.encode("utf-8")).hexdigest()
        conversation_id = context.conversation_scope
        model_name = context.model_name
        idempotency_key = context.idempotency_key
        correlation_id = context.correlation_id
        release_id = context.release_id
    error_class = {
        "cancelled": "CancelledError",
        "timeout": "TimeoutError",
        "failed": "RunError",
    }.get(terminal)
    return {
        "owner_user_id": owner_user_id,
        "agent_id": agent_id,
        "source": source,
        "credential_id": credential_id,
        "external_actor_hash": actor_hash,
        "conversation_id": conversation_id,
        "run_id": record.run_id,
        "release_id": release_id,
        "model": model_name,
        "input_tokens": int(getattr(record, "total_input_tokens", 0) or 0),
        "output_tokens": int(getattr(record, "total_output_tokens", 0) or 0),
        "total_tokens": int(getattr(record, "total_tokens", 0) or 0),
        "latency_ms": max(0, int(latency_ms)),
        "status": terminal,
        "error_class": error_class,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
    }


async def _settle_quota_with_retry(
    ledger: QuotaLedger,
    reservation_id: str,
    *,
    owner_user_id: str,
    run_id: str,
    terminal: str,
    usage: dict[str, Any],
) -> bool:
    """Retry transient settlement failures without losing the durable outbox."""
    for attempt in range(1, _SETTLEMENT_MAX_ATTEMPTS + 1):
        try:
            return await ledger.settle(
                reservation_id,
                owner_user_id=owner_user_id,
                tokens_used=usage["total_tokens"],
                status=terminal,
                run_id=run_id,
                usage=usage,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            if attempt == _SETTLEMENT_MAX_ATTEMPTS:
                logger.exception(
                    "Published Agent quota settlement exhausted retries",
                    extra={"reservation_id": reservation_id, "run_id": run_id},
                )
                return False
            logger.warning(
                "Published Agent quota settlement failed; retrying",
                extra={"reservation_id": reservation_id, "run_id": run_id, "attempt": attempt},
                exc_info=True,
            )
            await asyncio.sleep(_SETTLEMENT_RETRY_DELAY_SECONDS * (2 ** (attempt - 1)))
    return False


def _recovered_latency_ms(record: Any) -> int:
    try:
        started = datetime.fromisoformat(str((record.metadata or {})["published_settlement_started_at"]))
        finished = datetime.fromisoformat(str(record.updated_at))
    except (KeyError, TypeError, ValueError):
        return 0
    return max(0, int((finished - started).total_seconds() * 1000))


def _reservation_expired(reservation: dict[str, Any]) -> bool:
    value = reservation.get("expires_at")
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return False
    if not isinstance(value, datetime):
        return False
    expires_at = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return expires_at <= datetime.now(UTC)


async def recover_pending_quota_settlements(app: Any) -> int:
    """Settle terminal published Runs left in the durable outbox after restart."""
    repository = getattr(app.state, "agent_usage_repo", None)
    ledger = getattr(app.state, "quota_ledger", None)
    manager = getattr(app.state, "run_manager", None)
    if repository is None or ledger is None or manager is None:
        return 0
    from deerflow.persistence.agent_usage.sql import SYSTEM_SETTLEMENT_RECOVERY_SCOPE

    recovered = 0
    for reservation in await repository.list_pending_settlements(
        recovery_scope=SYSTEM_SETTLEMENT_RECOVERY_SCOPE,
    ):
        run_id = str(reservation["run_id"])
        owner_user_id = str(reservation["owner_user_id"])
        record = await manager.get(run_id, user_id=owner_user_id)
        if record is None:
            if not _reservation_expired(reservation):
                logger.warning(
                    "Published Agent settlement outbox is awaiting Run persistence",
                    extra={"reservation_id": reservation["id"], "run_id": run_id},
                )
                continue
            idempotency = getattr(app.state, "external_idempotency_repo", None)
            if idempotency is not None:
                try:
                    await idempotency.release_incomplete_by_run_id(
                        run_id=run_id,
                        user_id=owner_user_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to release incomplete idempotency claim for missing Run",
                        extra={"reservation_id": reservation["id"], "run_id": run_id},
                    )
                    continue
            if await ledger.release_unstarted(
                str(reservation["id"]),
                owner_user_id=owner_user_id,
                run_id=run_id,
            ):
                recovered += 1
                logger.warning(
                    "Released expired pre-bound reservation whose Run was never persisted",
                    extra={"reservation_id": reservation["id"], "run_id": run_id},
                )
            continue
        terminal = _terminal_status(record)
        if terminal is None and _reservation_expired(reservation):
            # PostgreSQL may retain an orphaned running row because global
            # startup reconciliation is unsafe with multiple Gateway replicas.
            # Once the reservation's max-run deadline has passed, settlement
            # can safely fail closed as timeout using the last durable snapshot.
            terminal = "timeout"
        if terminal is None:
            continue
        try:
            usage = _usage_from_record(
                record,
                owner_user_id=owner_user_id,
                terminal=terminal,
                latency_ms=_recovered_latency_ms(record),
            )
        except (KeyError, TypeError, ValueError):
            logger.exception(
                "Published Agent settlement outbox Run is missing trusted metadata",
                extra={"reservation_id": reservation["id"], "run_id": run_id},
            )
            continue
        if await _settle_quota_with_retry(
            ledger,
            str(reservation["id"]),
            owner_user_id=owner_user_id,
            run_id=run_id,
            terminal=terminal,
            usage=usage,
        ):
            recovered += 1
    return recovered


async def run_quota_settlement_recovery_loop(
    app: Any,
    *,
    interval_seconds: float = 30.0,
) -> None:
    """Periodically recover terminal or expired settlement outbox rows."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            recovered = await recover_pending_quota_settlements(app)
            if recovered:
                logger.info("Recovered %d published-Agent quota settlement(s)", recovered)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Periodic published-Agent quota settlement recovery failed")


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
                # Bind the claim to its Run identity before starting work. If
                # response serialization or completion persistence fails, a
                # retry can still resolve this exact Run instead of creating a
                # duplicate or waiting for claim expiry.
                "run_id": str(uuid4()),
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
    # Caller-provided correlation/request ids are observability data, not a
    # reservation identity. Only an Idempotency-Key may deliberately reuse a
    # reservation; every ordinary request gets a server-generated attempt id.
    identity = f"idempotency:{context.idempotency_key}" if context.idempotency_key else f"attempt:{uuid4().hex}"
    payload = f"{context.agent_id}:{context.credential_id}:{operation}:{identity}"
    return hashlib.sha256(payload.encode()).hexdigest()


async def _reserve_quota(
    *,
    ledger: QuotaLedger,
    context: PublishedAgentContext,
    body: AgentRunCreateRequest,
    operation: str,
    run_id: str,
) -> Reservation:
    quota = context.effective_quota
    if body.message_utf8_bytes() > quota.max_input_bytes:
        raise HTTPException(status_code=413, detail={"code": "input_too_large"})
    try:
        return await ledger.reserve(
            context,
            request_key=_quota_request_key(context=context, operation=operation),
            run_id=run_id,
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
            # RunManager.cancel() signals/cancels but intentionally does not
            # join the worker. Await it here so worker.finally can flush journal
            # token totals before this settlement snapshots the RunRecord.
            if record.task is not None:
                try:
                    await record.task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    # The terminal mapping below already accounts for failure;
                    # settlement itself must still proceed.
                    pass
        except asyncio.CancelledError:
            # Gateway shutdown leaves the reservation pending; expiry cleanup
            # releases it on the next reserve rather than mis-accounting it.
            if record.status != RunStatus.interrupted:
                return
        except Exception:
            pass

        terminal = "timeout" if timed_out else (_terminal_status(record) or "failed")
        usage = _usage_from_record(
            record,
            owner_user_id=context.owner_user_id,
            terminal=terminal,
            latency_ms=max(0, int((time.perf_counter() - started_at) * 1000)),
            context=context,
        )
        settled = await _settle_quota_with_retry(
            ledger,
            reservation.id,
            owner_user_id=context.owner_user_id,
            run_id=record.run_id,
            terminal=terminal,
            usage=usage,
        )
        if not settled:
            return
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
    scope = _request_scope(request, agent_id=agent_id, conversation_id=conversation_id)
    conversation = await _conversation_or_404(
        conversations,
        scope=scope,
    )
    if conversation["status"] != "active":
        raise HTTPException(status_code=409, detail={"code": "conversation_closed"})

    context = await _resolve_context(
        resolver=resolver,
        agent_id=agent_id,
        request=request,
        conversation_scope=conversation_id,
        idempotency_key=idempotency_key,
    )
    # Resolve all lifecycle/Release authority before claiming the key. A
    # missing, suspended, or invalid Agent must remain immediately retryable.
    replay, claimed = await _claim_run(
        idempotency,
        request=request,
        operation=operation,
        body=body,
        idempotency_key=idempotency_key,
    )
    if replay is not None and replay.get("response_json"):
        run_id = str(replay.get("run_id") or replay["response_json"]["run_id"])
        return await _run_or_404(request, run_id, scope=scope), True
    if replay is not None and not claimed and replay.get("run_id"):
        try:
            return await _run_or_404(request, str(replay["run_id"]), scope=scope), True
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            # The claim is bound, but its first request has not durably created
            # the Run yet. Preserve the existing in-progress contract during
            # this narrow concurrent-start window.
            raise HTTPException(status_code=409, detail={"code": "idempotency_in_progress"}) from exc
    if replay is not None and not claimed:
        raise HTTPException(status_code=409, detail={"code": "idempotency_in_progress"})

    run_id = str(replay["run_id"]) if claimed and replay is not None and replay.get("run_id") else str(uuid4())
    try:
        reservation = await _reserve_quota(
            ledger=quota_ledger,
            context=context,
            body=body,
            operation=operation,
            run_id=run_id,
        )
    except Exception:
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
            run_id=run_id,
        )
    except asyncio.CancelledError:
        cleanup = [
            quota_ledger.release_unstarted(
                reservation.id,
                owner_user_id=context.owner_user_id,
                run_id=run_id,
            )
        ]
        if claimed and idempotency_key is not None:
            cleanup.append(
                idempotency.release(
                    api_key_id=_credential_id(request),
                    idempotency_key=idempotency_key,
                )
            )
        cleanup_task = asyncio.ensure_future(asyncio.gather(*cleanup, return_exceptions=True))
        try:
            results = await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            results = await cleanup_task
        for result in results:
            if isinstance(result, BaseException):
                logger.error("Cancelled published Run startup cleanup failed", exc_info=result)
        raise
    except HTTPException as exc:
        await quota_ledger.release_unstarted(
            reservation.id,
            owner_user_id=context.owner_user_id,
            run_id=run_id,
        )
        if claimed and idempotency_key is not None:
            await idempotency.release(
                api_key_id=_credential_id(request),
                idempotency_key=idempotency_key,
            )
        if exc.status_code == 409:
            raise HTTPException(status_code=409, detail={"code": "conversation_busy"}) from exc
        raise
    except Exception:
        await quota_ledger.release_unstarted(
            reservation.id,
            owner_user_id=context.owner_user_id,
            run_id=run_id,
        )
        if claimed and idempotency_key is not None:
            await idempotency.release(
                api_key_id=_credential_id(request),
                idempotency_key=idempotency_key,
            )
        raise

    # From this point the Run is executing independently. Install settlement
    # before response serialization/idempotency persistence so either layer
    # failing cannot strand its quota reservation.
    _schedule_quota_settlement(
        request=request,
        record=record,
        reservation=reservation,
        context=context,
        ledger=quota_ledger,
    )
    response = serialize_agent_run(record, conversation_id=conversation_id)
    if claimed and idempotency_key is not None:
        await idempotency.complete(
            api_key_id=_credential_id(request),
            idempotency_key=idempotency_key,
            run_id=record.run_id,
            response_status=202,
            response_json=response,
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
) -> AsyncIterator[str]:
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
    agents: PublishedAgentRepository = Depends(get_published_agent_repo),
) -> dict[str, Any]:
    """Return explicitly whitelisted metadata for one runnable Agent."""
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


@router.get("/capabilities")
async def get_agent_capabilities(
    agent_id: str,
    request: Request,
    resolver: PublishedAgentResolver = Depends(get_published_agent_resolver),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    """Return safe Skill names and the active published model capability."""
    context = await _resolve_context(
        resolver=resolver,
        agent_id=agent_id,
        request=request,
        conversation_scope="capabilities",
    )
    model = config.get_model_config(context.model_name)
    return serialize_agent_capabilities(
        agent_id,
        skills=tuple((item.name, item.display_name, item.description) for item in context.skill_metadata),
        model_name=context.model_name,
        model_display_name=model.display_name if model is not None else None,
        supports_thinking=bool(model.supports_thinking) if model is not None else False,
        supports_reasoning_effort=bool(model.supports_reasoning_effort) if model is not None else False,
        supports_vision=bool(model.supports_vision) if model is not None else False,
        model_available=model is not None,
    )


@router.post("/conversations", status_code=201)
async def create_agent_conversation(
    agent_id: str,
    body: AgentConversationCreateRequest,
    request: Request,
    repository: ExternalConversationRepository = Depends(get_external_conversation_repo),
    resolver: PublishedAgentResolver = Depends(get_published_agent_resolver),
) -> dict[str, Any]:
    """Create a conversation isolated to the authenticated Agent credential."""
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
            source=_conversation_source(request),
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
    """Return a conversation only within the authenticated credential scope."""
    await _resolve_context(
        resolver=resolver,
        agent_id=agent_id,
        request=request,
        conversation_scope=conversation_id,
    )
    row = await _conversation_or_404(
        repository,
        scope=_request_scope(request, agent_id=agent_id, conversation_id=conversation_id),
    )
    return serialize_agent_conversation(row)


@router.post("/conversations/{conversation_id}/uploads")
async def upload_files_to_agent_conversation(
    agent_id: str,
    conversation_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    repository: ExternalConversationRepository = Depends(get_external_conversation_repo),
    resolver: PublishedAgentResolver = Depends(get_published_agent_resolver),
    config: AppConfig = Depends(get_config),
):
    """Upload files attached to a published-Agent conversation (Agent Key auth).

    The returned ``files[].path`` can be passed straight into the
    ``message`` list of a subsequent run request as a ``file_path`` content
    part::

        {"message": [
          {"type": "text", "text": "请分析附件"},
          {"type": "file_path", "path": "<returned path>", "name": "photo.jpg"}
        ]}
    """
    # Resolve + validate context up-front → 404 / 410 / agent_not_found for
    # bad agent_id before any filesystem write takes place.
    await _resolve_context(
        resolver=resolver,
        agent_id=agent_id,
        request=request,
        conversation_scope=conversation_id,
    )
    scope = _request_scope(request, agent_id=agent_id, conversation_id=conversation_id)
    row = await _conversation_or_404(
        repository,
        scope=scope,
    )
    # ExternalConversationRepository rows expose the internal thread id that
    # already backs this conversation.  Uploads go directly there so both the
    # UI thread-view and the runtime worker's uploads path resolve to the
    # same filesystem location.
    thread_id = row["thread_id"] if isinstance(row, dict) else getattr(row, "thread_id")
    from app.gateway.routers.uploads import UploadContext, perform_upload_for_thread

    ctx = UploadContext(
        thread_id=str(thread_id),
        effective_user_id=str(scope.owner_user_id),
        app_config=config,
    )
    return await perform_upload_for_thread(ctx=ctx, files=files)


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
    """Start an asynchronous published-Agent run."""
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
    """Start a run and wait for its terminal public representation."""
    idempotency_key = _validate_idempotency_key(idempotency_key)
    record, _replayed = await _start_public_run(
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
    if record.task is not None:
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
    """Start a run and expose its sanitized event stream as SSE."""
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
            "Content-Location": (f"/api/v1/agents/{agent_id}/conversations/{conversation_id}/runs/{record.run_id}"),
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
    """Return one run within its Agent, credential, and conversation scope."""
    await _resolve_context(
        resolver=resolver,
        agent_id=agent_id,
        request=request,
        conversation_scope=conversation_id,
    )
    await _conversation_or_404(
        conversations,
        scope=(scope := _request_scope(request, agent_id=agent_id, conversation_id=conversation_id)),
    )
    row = await _run_or_404(
        request,
        run_id,
        scope=scope,
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
    """Cancel a scoped run and return its latest public state."""
    await _resolve_context(
        resolver=resolver,
        agent_id=agent_id,
        request=request,
        conversation_scope=conversation_id,
    )
    await _conversation_or_404(
        conversations,
        scope=(scope := _request_scope(request, agent_id=agent_id, conversation_id=conversation_id)),
    )
    row = await _run_or_404(
        request,
        run_id,
        scope=scope,
    )
    if row.status in (RunStatus.pending, RunStatus.running, RunStatus.interrupted):
        await get_run_manager(request).cancel(run_id)
    updated = await _run_or_404(
        request,
        run_id,
        scope=scope,
    )
    return serialize_agent_run(updated, conversation_id=conversation_id)
