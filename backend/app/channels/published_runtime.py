"""Published-Agent execution orchestration for DB-driven IM bindings."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
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
_PROGRESS_DRAIN_TIMEOUT_SECONDS = 0.25
_RUN_CLEANUP_TIMEOUT_SECONDS = 1.0
PUBLISHED_INBOUND_MAX_FILES = 10
PUBLISHED_INBOUND_MAX_FILE_BYTES = 50 * 1024 * 1024


class PublishedChannelUnavailableError(RuntimeError):
    """The stable binding cannot currently resolve to a runnable Agent."""


class PublishedChannelBusyError(RuntimeError):
    """The binding's effective quota rejected this inbound attempt."""


class PublishedRunDetachedError(RuntimeError):
    """A started Run could not be joined; its reservation remains recoverable."""


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
    artifacts: tuple[str, ...] = ()
    owner_user_id: str = ""


@dataclass(frozen=True)
class PublishedInboundPreparation:
    """Materialized inbound message and its measured attachment byte count."""

    message: InboundMessage
    attachment_bytes: int = 0


PublishedInboundPreparer = Callable[
    [InboundMessage, str, str, int],
    Awaitable[PublishedInboundPreparation],
]
PublishedProgressCallback = Callable[[str, str], Awaitable[None]]
ExecutorProgressCallback = Callable[[str], Awaitable[None]]


class MappingStoreLike(Protocol):
    """Persist stable channel-to-thread mappings behind a trusted scope."""

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
    ) -> str:
        """Resolve one stable conversation thread for a persisted binding.

        Args:
            system_scope: Unforgeable system authority for cross-owner ingress.

        Returns:
            The existing or atomically created runtime thread ID.

        Raises:
            PermissionError: The caller does not hold system authority.
            MappingScopeConflictError: The binding is absent or assigned to a
                different Agent.
        """
        ...


class ResolverLike(Protocol):
    """Resolve immutable Published-Agent execution context for one ingress."""

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
    ) -> PublishedAgentContext:
        """Authorize the binding and pin its currently published Release.

        Returns:
            An owner-scoped, memory-disabled context containing the effective
            quota and immutable release capabilities.

        Raises:
            AgentNotAvailableError: The Agent has no runnable Release.
            AgentSuspendedError: The Agent or Release is suspended.
        """
        ...


class QuotaLedgerLike(Protocol):
    """Manage reservation lifecycle and exactly-once terminal usage."""

    async def reserve(
        self,
        context: PublishedAgentContext,
        *,
        request_key: str,
        run_id: str | None = None,
    ) -> Reservation:
        """Acquire quota once for a deterministic request and optional Run ID.

        Raises:
            QuotaExceededError: An effective concurrency, rate, run, or token
                limit rejects the request.
        """
        ...

    async def settle(
        self,
        reservation_id: str,
        *,
        owner_user_id: str,
        tokens_used: int,
        status: str,
        run_id: str | None = None,
        usage: dict[str, object] | None = None,
    ) -> bool:
        """Settle one Run-bound reservation and write one terminal usage row.

        Returns:
            ``True`` when this call performs settlement; ``False`` for an
            already-settled idempotent replay.
        """
        ...

    async def release(self, reservation_id: str, *, owner_user_id: str) -> bool:
        """Release only an unbound reservation that has no durable Run."""
        ...

    async def release_unstarted(
        self,
        reservation_id: str,
        *,
        owner_user_id: str,
        run_id: str,
    ) -> bool:
        """Release a pre-bound reservation after proving its Run never started."""
        ...


class PublishedRunExecutor(Protocol):
    """Run one pinned Release and return a terminal, account-ready result."""

    async def execute(
        self,
        *,
        run_id: str,
        thread_id: str,
        message: str,
        context: PublishedAgentContext,
        reservation: Reservation,
        on_progress: ExecutorProgressCallback | None = None,
    ) -> PublishedChannelExecution:
        """Execute the Run, forwarding throttled text snapshots when requested.

        The executor owns a started Run until it reaches terminal state. If its
        dispatcher is cancelled, it must cancel and join the Run, or raise
        ``PublishedRunDetachedError`` so the reservation remains recoverable.

        Returns:
            Terminal text, artifacts, token counts, status, and latency.
        """
        ...


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
    ) -> RunRecord:
        """Create a Gateway Run using pre-authorized published context."""
        ...


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

    async def run(
        self,
        message: InboundMessage,
        *,
        prepare_inbound: PublishedInboundPreparer | None = None,
        on_progress: PublishedProgressCallback | None = None,
    ) -> PublishedChannelExecution:
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

        text_bytes = len(message.text.encode("utf-8"))
        if len(message.files) > PUBLISHED_INBOUND_MAX_FILES:
            raise PublishedChannelBusyError("published Agent input has too many attachments")
        declared_attachment_bytes = 0
        for file_info in message.files:
            declared_size = file_info.get("size") if isinstance(file_info, dict) else None
            if not isinstance(declared_size, int) or isinstance(declared_size, bool) or declared_size < 0:
                continue
            if declared_size > PUBLISHED_INBOUND_MAX_FILE_BYTES:
                raise PublishedChannelBusyError("published Agent attachment exceeds its size limit")
            declared_attachment_bytes += declared_size
        if text_bytes + declared_attachment_bytes > context.effective_quota.max_input_bytes:
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
            if prepare_inbound is not None:
                try:
                    prepared = await prepare_inbound(
                        message,
                        thread_id,
                        context.owner_user_id,
                        context.effective_quota.max_input_bytes,
                    )
                except Exception as exc:
                    raise PublishedChannelBusyError("published Agent attachment could not be admitted") from exc
                message = prepared.message
                effective_input_bytes = len(message.text.encode("utf-8")) + prepared.attachment_bytes
                if effective_input_bytes > context.effective_quota.max_input_bytes:
                    raise PublishedChannelBusyError("published Agent input exceeds its quota")

            async def publish_progress(text: str) -> None:
                if on_progress is not None:
                    await on_progress(thread_id, text)

            execution = await self._executor.execute(
                run_id=run_id,
                thread_id=thread_id,
                message=message.text,
                context=context,
                reservation=reservation,
                on_progress=publish_progress if on_progress is not None else None,
            )
        except PublishedRunDetachedError:
            # The Run exists and may still consume tokens. Keep its reservation
            # pending so the durable settlement recovery can reconcile it.
            raise
        except BaseException:
            await self._quota.release_unstarted(
                reservation.id,
                owner_user_id=context.owner_user_id,
                run_id=run_id,
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
        return replace(execution, owner_user_id=context.owner_user_id)


class GatewayPublishedRunExecutor:
    """Execute one published channel Run through Gateway's trusted runtime path."""

    def __init__(
        self,
        app: FastAPI,
        *,
        run_starter: GatewayRunStarter | None = None,
        cleanup_timeout_seconds: float = _RUN_CLEANUP_TIMEOUT_SECONDS,
    ) -> None:
        if cleanup_timeout_seconds <= 0:
            raise ValueError("cleanup_timeout_seconds must be positive")
        self._app = app
        self._run_starter = run_starter
        self._cleanup_timeout_seconds = cleanup_timeout_seconds

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

    async def _cancel_started_run(self, record: RunRecord) -> None:
        """Cancel and join a started Run or fail without releasing its quota."""
        cancel_task = asyncio.create_task(self._app.state.run_manager.cancel(record.run_id))
        try:
            done, _pending = await asyncio.wait(
                {cancel_task},
                timeout=self._cleanup_timeout_seconds,
            )
        except asyncio.CancelledError:
            cancel_task.cancel()
            cancel_task.add_done_callback(self._consume_task_result)
            raise
        if not done:
            cancel_task.cancel()
            cancel_task.add_done_callback(self._consume_task_result)
            raise TimeoutError("RunManager cancellation exceeded its cleanup deadline")
        cancelled = await cancel_task
        task = record.task
        if cancelled is False and task is not None and not task.done():
            raise RuntimeError("RunManager did not confirm cancellation")
        if task is not None:
            await self._join_cleanup_task(task, label="Run worker")

    @staticmethod
    def _consume_task_result(task: asyncio.Task[object]) -> None:
        """Retrieve a detached cleanup task result to avoid loop warnings."""
        try:
            task.result()
        except BaseException:
            pass

    async def _join_cleanup_task(self, task: asyncio.Task[object], *, label: str) -> None:
        """Join a cleanup task without allowing it to extend the deadline."""
        done, _pending = await asyncio.wait(
            {task},
            timeout=self._cleanup_timeout_seconds,
        )
        if not done:
            task.add_done_callback(self._consume_task_result)
            raise TimeoutError(f"{label} exceeded its cleanup deadline")
        await asyncio.gather(task, return_exceptions=True)

    async def execute(
        self,
        *,
        run_id: str,
        thread_id: str,
        message: str,
        context: PublishedAgentContext,
        reservation: Reservation,
        on_progress: ExecutorProgressCallback | None = None,
    ) -> PublishedChannelExecution:
        """Start and own one Run, preserving quota after durable creation.

        Once ``run_starter`` returns, every otherwise-unhandled exception or
        cancellation is converted to :class:`PublishedRunDetachedError`. The
        outer reservation owner can therefore release quota only when this
        method fails before the Run starter succeeds.
        """
        started = False

        def mark_started() -> None:
            nonlocal started
            started = True

        try:
            return await self._execute(
                run_id=run_id,
                thread_id=thread_id,
                message=message,
                context=context,
                reservation=reservation,
                on_progress=on_progress,
                _on_started=mark_started,
            )
        except PublishedRunDetachedError:
            raise
        except BaseException as exc:
            if started:
                raise PublishedRunDetachedError("started Run finalization could not be confirmed") from exc
            raise

    async def _execute(
        self,
        *,
        run_id: str,
        thread_id: str,
        message: str,
        context: PublishedAgentContext,
        reservation: Reservation,
        on_progress: ExecutorProgressCallback | None = None,
        _on_started: Callable[[], None],
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
        _on_started()
        last_values: dict[str, object] | list[object] | None = None
        stream_task: asyncio.Task[None] | None = None
        progress_task: asyncio.Task[None] | None = None
        progress_queue: asyncio.Queue[str] | None = None
        bridge = getattr(self._app.state, "stream_bridge", None)
        if bridge is not None:
            from app.channels.manager import _accumulate_stream_text, _extract_response_text

            if on_progress is not None:
                progress_queue = asyncio.Queue(maxsize=1)

                async def deliver_progress() -> None:
                    assert progress_queue is not None
                    while True:
                        progress_text = await progress_queue.get()
                        try:
                            await on_progress(progress_text)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            logger.exception(
                                "Published Feishu progress callback failed",
                                extra={"run_id": record.run_id},
                            )
                        finally:
                            progress_queue.task_done()

                progress_task = asyncio.create_task(deliver_progress())

            async def consume_stream() -> None:
                nonlocal last_values
                buffers: dict[str, str] = {}
                current_message_id: str | None = None
                latest_text = ""
                last_published_text = ""
                last_publish_at = 0.0
                async for event in bridge.subscribe(record.run_id):
                    if event.event == "messages-tuple":
                        accumulated_text, current_message_id = _accumulate_stream_text(
                            buffers,
                            current_message_id,
                            event.data,
                        )
                        if accumulated_text:
                            latest_text = accumulated_text
                    elif event.event == "values" and isinstance(event.data, (dict, list)):
                        last_values = event.data
                        snapshot_text = _extract_response_text(event.data)
                        if snapshot_text:
                            latest_text = snapshot_text
                    elif event.event == "__end__":
                        return

                    if on_progress is None or not latest_text or latest_text == last_published_text:
                        continue
                    now = time.monotonic()
                    if last_published_text and now - last_publish_at < 0.35:
                        continue
                    assert progress_queue is not None
                    if progress_queue.full():
                        try:
                            progress_queue.get_nowait()
                            progress_queue.task_done()
                        except asyncio.QueueEmpty:
                            pass
                    progress_queue.put_nowait(latest_text)
                    last_published_text = latest_text
                    last_publish_at = now

            stream_task = asyncio.create_task(consume_stream())
        timed_out = False
        if record.task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(record.task),
                    timeout=context.effective_quota.max_run_seconds,
                )
            except TimeoutError:
                timed_out = True
                try:
                    await self._cancel_started_run(record)
                except asyncio.CancelledError as exc:
                    raise PublishedRunDetachedError("started Run timeout cleanup failed") from exc
                except Exception as exc:
                    raise PublishedRunDetachedError("started Run timeout cleanup failed") from exc
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    try:
                        await self._cancel_started_run(record)
                    except asyncio.CancelledError as exc:
                        raise PublishedRunDetachedError("dispatcher cancellation interrupted Run cleanup") from exc
                    except Exception as exc:
                        raise PublishedRunDetachedError("started Run could not be cancelled") from exc
                # Otherwise the Run task itself was interrupted while this
                # dispatcher remains live; preserve its terminal status below.
            except Exception:
                pass

        if stream_task is not None:
            try:
                # A Run worker publishes ``__end__`` before its task completes.
                # Once the worker is joined, drain that retained terminal log
                # without an arbitrary short timeout that could discard final
                # values and artifacts under scheduler or I/O pressure.
                await asyncio.shield(stream_task)
            except asyncio.CancelledError as exc:
                if not stream_task.cancelled():
                    raise PublishedRunDetachedError("dispatcher cancellation interrupted stream cleanup") from exc
                logger.warning(
                    "Published Feishu stream consumer was cancelled",
                    extra={"run_id": record.run_id},
                )
            except Exception:
                logger.warning(
                    "Published Feishu stream consumer failed",
                    extra={"run_id": record.run_id},
                    exc_info=True,
                )

        if progress_task is not None:
            assert progress_queue is not None
            drain_cancelled: asyncio.CancelledError | None = None
            try:
                await asyncio.wait_for(
                    progress_queue.join(),
                    timeout=_PROGRESS_DRAIN_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logger.info(
                    "Dropping slow published Feishu progress update",
                    extra={"run_id": record.run_id},
                )
            except asyncio.CancelledError as exc:
                drain_cancelled = exc
            finally:
                progress_task.cancel()
                try:
                    await self._join_cleanup_task(progress_task, label="progress callback")
                except asyncio.CancelledError as exc:
                    raise PublishedRunDetachedError("dispatcher cancellation interrupted progress cleanup") from exc
                except TimeoutError as exc:
                    raise PublishedRunDetachedError("progress cleanup exceeded its deadline") from exc
            if drain_cancelled is not None:
                raise PublishedRunDetachedError("dispatcher cancellation interrupted progress cleanup") from drain_cancelled

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
        artifacts: tuple[str, ...] = ()
        if last_values is not None:
            from app.channels.manager import _extract_artifacts

            artifacts = tuple(_extract_artifacts(last_values))
        return PublishedChannelExecution(
            run_id=record.run_id,
            thread_id=thread_id,
            text=text,
            status=status,
            input_tokens=int(record.total_input_tokens or 0),
            output_tokens=int(record.total_output_tokens or 0),
            total_tokens=int(record.total_tokens or 0),
            latency_ms=max(0, int((time.perf_counter() - started_at) * 1000)),
            artifacts=artifacts,
        )


__all__ = [
    "GatewayPublishedRunExecutor",
    "PublishedChannelBusyError",
    "PublishedChannelExecution",
    "PublishedChannelRuntime",
    "PublishedChannelUnavailableError",
    "PublishedRunExecutor",
]
