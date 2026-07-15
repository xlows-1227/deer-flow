"""Published-Agent execution orchestration for DB-driven IM bindings."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from fastapi import FastAPI
from starlette.requests import Request

from app.channels.message_bus import InboundMessage
from deerflow.persistence.channel_mapping import SYSTEM_CHANNEL_MAPPING_SCOPE
from deerflow.publishing.context import PublishedAgentContext
from deerflow.publishing.quota import QuotaExceededError, Reservation
from deerflow.publishing.resolver import AgentNotAvailableError, AgentSuspendedError
from deerflow.runtime import RunRecord

logger = logging.getLogger(__name__)

_SETTLEMENT_MAX_ATTEMPTS = 5
_SETTLEMENT_RETRY_DELAY_SECONDS = 0.05


class PublishedChannelUnavailableError(RuntimeError):
    """The stable binding cannot currently resolve to a runnable Agent."""


class PublishedChannelBusyError(RuntimeError):
    """The binding's effective quota rejected this inbound attempt."""


@dataclass(frozen=True)
class PublishedChannelExecution:
    """Safe terminal result returned by a published runtime executor."""

    run_id: str
    thread_id: str
    text: str
    status: Literal["success", "cancelled", "timeout", "failed"]
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: int


class MappingStoreLike(Protocol):
    async def get_or_create_thread(
        self,
        *,
        binding_id: str,
        agent_id: str,
        chat_id: str,
        feishu_user_id: str,
        chat_type: Literal["p2p", "group"],
        topic_id: str | None,
        system_scope: object,
    ) -> str: ...


class ResolverLike(Protocol):
    async def resolve(
        self,
        agent_id: str,
        *,
        source: Literal["feishu"],
        credential_id: str,
        external_actor: str,
        conversation_scope: str,
        correlation_id: str,
        idempotency_key: str | None = None,
    ) -> PublishedAgentContext: ...


class QuotaLedgerLike(Protocol):
    async def reserve(
        self,
        context: PublishedAgentContext,
        *,
        request_key: str,
        run_id: str | None = None,
    ) -> Reservation: ...

    async def settle(
        self,
        reservation_id: str,
        *,
        owner_user_id: str,
        tokens_used: int,
        status: str,
        run_id: str | None = None,
        usage: dict[str, object] | None = None,
    ) -> bool: ...

    async def release(self, reservation_id: str, *, owner_user_id: str) -> bool: ...


class PublishedRunExecutor(Protocol):
    async def execute(
        self,
        *,
        run_id: str,
        thread_id: str,
        message: str,
        context: PublishedAgentContext,
        reservation: Reservation,
    ) -> PublishedChannelExecution: ...


class GatewayRunStarter(Protocol):
    """Gateway Run creation seam used by the channel execution adapter."""

    async def __call__(
        self,
        body: object,
        thread_id: str,
        request: Request,
        *,
        published_context: PublishedAgentContext | None = None,
        run_id: str | None = None,
    ) -> RunRecord: ...


class PublishedChannelRuntime:
    """Compose mapping, resolution, quota, execution and usage settlement."""

    def __init__(
        self,
        *,
        mapping_store: MappingStoreLike,
        resolver: ResolverLike,
        quota_ledger: QuotaLedgerLike,
        executor: PublishedRunExecutor,
    ) -> None:
        self._mappings = mapping_store
        self._resolver = resolver
        self._quota = quota_ledger
        self._executor = executor

    @staticmethod
    def _metadata_value(message: InboundMessage, key: str) -> str:
        value = message.metadata.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PublishedChannelUnavailableError(f"missing trusted {key}")
        return value.strip()

    async def _settle_terminal(
        self,
        reservation: Reservation,
        *,
        context: PublishedAgentContext,
        execution: PublishedChannelExecution,
        usage: dict[str, object],
    ) -> None:
        """Retry transient accounting failures while durable recovery stays armed."""
        for attempt in range(1, _SETTLEMENT_MAX_ATTEMPTS + 1):
            try:
                await self._quota.settle(
                    reservation.id,
                    owner_user_id=context.owner_user_id,
                    tokens_used=execution.total_tokens,
                    status=execution.status,
                    run_id=execution.run_id,
                    usage=usage,
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt == _SETTLEMENT_MAX_ATTEMPTS:
                    logger.exception(
                        "Published Feishu usage settlement exhausted retries",
                        extra={"reservation_id": reservation.id, "run_id": execution.run_id},
                    )
                    return
                logger.warning(
                    "Published Feishu usage settlement failed; retrying",
                    extra={
                        "reservation_id": reservation.id,
                        "run_id": execution.run_id,
                        "attempt": attempt,
                    },
                    exc_info=True,
                )
                await asyncio.sleep(_SETTLEMENT_RETRY_DELAY_SECONDS * (2 ** (attempt - 1)))

    async def run(self, message: InboundMessage) -> PublishedChannelExecution:
        """Execute one verified and deduplicated binding message exactly once."""
        binding_id = self._metadata_value(message, "binding_id")
        agent_id = self._metadata_value(message, "agent_id")
        event_id = self._metadata_value(message, "event_id")
        chat_type = self._metadata_value(message, "chat_type")
        if chat_type not in {"p2p", "group"}:
            raise PublishedChannelUnavailableError("invalid trusted chat_type")
        chat_scope: Literal["p2p", "group"] = "p2p" if chat_type == "p2p" else "group"

        thread_id = await self._mappings.get_or_create_thread(
            binding_id=binding_id,
            agent_id=agent_id,
            chat_id=message.chat_id,
            feishu_user_id=message.user_id,
            chat_type=chat_scope,
            topic_id=message.topic_id,
            system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE,
        )
        try:
            context = await self._resolver.resolve(
                agent_id,
                source="feishu",
                credential_id=binding_id,
                external_actor=f"feishu:{message.user_id}",
                conversation_scope=thread_id,
                correlation_id=event_id,
                idempotency_key=None,
            )
        except (AgentNotAvailableError, AgentSuspendedError) as exc:
            raise PublishedChannelUnavailableError("published Agent is unavailable") from exc

        if len(message.text.encode("utf-8")) > context.effective_quota.max_input_bytes:
            raise PublishedChannelBusyError("published Agent input exceeds its quota")
        run_id = str(uuid4())
        request_key = hashlib.sha256(f"feishu:{binding_id}:{event_id}".encode()).hexdigest()
        try:
            reservation = await self._quota.reserve(
                context,
                request_key=request_key,
                run_id=run_id,
            )
        except QuotaExceededError as exc:
            raise PublishedChannelBusyError(exc.code) from exc

        try:
            execution = await self._executor.execute(
                run_id=run_id,
                thread_id=thread_id,
                message=message.text,
                context=context,
                reservation=reservation,
            )
        except BaseException:
            await self._quota.release(
                reservation.id,
                owner_user_id=context.owner_user_id,
            )
            raise

        error_class = {
            "cancelled": "CancelledError",
            "timeout": "TimeoutError",
            "failed": "RunError",
        }.get(execution.status)
        usage: dict[str, object] = {
            "owner_user_id": context.owner_user_id,
            "agent_id": context.agent_id,
            "source": "feishu",
            "credential_id": binding_id,
            "external_actor_hash": hashlib.sha256(context.external_actor.encode()).hexdigest(),
            "conversation_id": thread_id,
            "run_id": execution.run_id,
            "model": context.model_name,
            "input_tokens": execution.input_tokens,
            "output_tokens": execution.output_tokens,
            "total_tokens": execution.total_tokens,
            "latency_ms": execution.latency_ms,
            "status": execution.status,
            "error_class": error_class,
            "idempotency_key": None,
            "correlation_id": event_id,
        }
        await self._settle_terminal(
            reservation,
            context=context,
            execution=execution,
            usage=usage,
        )
        return execution


class GatewayPublishedRunExecutor:
    """Execute one published channel Run through Gateway's trusted runtime path."""

    def __init__(
        self,
        app: FastAPI,
        *,
        run_starter: GatewayRunStarter | None = None,
    ) -> None:
        self._app = app
        self._run_starter = run_starter

    @staticmethod
    def _request(app: FastAPI) -> Request:
        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/internal/channels/feishu/run",
                "raw_path": b"/internal/channels/feishu/run",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 0),
                "server": ("127.0.0.1", 0),
                "app": app,
            }
        )

    async def execute(
        self,
        *,
        run_id: str,
        thread_id: str,
        message: str,
        context: PublishedAgentContext,
        reservation: Reservation,
    ) -> PublishedChannelExecution:
        """Start, await and safely serialize one memory-free published Run."""
        from app.gateway.routers.thread_runs import RunCreateRequest
        from deerflow.runtime import RunStatus

        run_starter = self._run_starter
        if run_starter is None:
            from app.gateway.services import start_run

            run_starter = start_run

        metadata = {
            "published_agent": True,
            "published_agent_id": context.agent_id,
            "published_credential_id": context.credential_id,
            "published_conversation_id": thread_id,
            "published_release_id": context.release_id,
            "published_correlation_id": context.correlation_id,
            "published_idempotency_key": context.idempotency_key,
            "published_quota_reservation_id": reservation.id,
            "published_source": "feishu",
            "published_external_actor_hash": hashlib.sha256(context.external_actor.encode()).hexdigest(),
            "published_model_name": context.model_name,
            "published_settlement_started_at": datetime.now(UTC).isoformat(),
        }
        body = RunCreateRequest(
            assistant_id="lead_agent",
            input={"messages": [{"role": "user", "content": message}]},
            metadata=metadata,
            stream_mode=["values", "messages-tuple", "custom"],
            on_disconnect="continue",
            multitask_strategy="reject",
        )
        started_at = time.perf_counter()
        record = await run_starter(
            body,
            thread_id,
            self._request(self._app),
            published_context=context,
            run_id=run_id,
        )
        timed_out = False
        if record.task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(record.task),
                    timeout=context.effective_quota.max_run_seconds,
                )
            except TimeoutError:
                timed_out = True
                await self._app.state.run_manager.cancel(record.run_id)
                if record.task is not None:
                    try:
                        await record.task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise
                # The Run task itself was interrupted while this dispatcher
                # remains live. Preserve its terminal status for settlement.
            except Exception:
                pass

        if timed_out or record.status == RunStatus.timeout:
            status: Literal["success", "cancelled", "timeout", "failed"] = "timeout"
            text = "The request timed out. Please try again."
        elif record.status == RunStatus.success:
            status = "success"
            text = record.last_ai_message or "(No response from agent)"
        elif record.status == RunStatus.interrupted:
            status = "cancelled"
            text = "The request was cancelled. Please try again."
        else:
            status = "failed"
            text = "The agent could not complete the request. Please try again."
        return PublishedChannelExecution(
            run_id=record.run_id,
            thread_id=thread_id,
            text=text,
            status=status,
            input_tokens=int(record.total_input_tokens or 0),
            output_tokens=int(record.total_output_tokens or 0),
            total_tokens=int(record.total_tokens or 0),
            latency_ms=max(0, int((time.perf_counter() - started_at) * 1000)),
        )


__all__ = [
    "GatewayPublishedRunExecutor",
    "PublishedChannelBusyError",
    "PublishedChannelExecution",
    "PublishedChannelRuntime",
    "PublishedChannelUnavailableError",
    "PublishedRunExecutor",
]
