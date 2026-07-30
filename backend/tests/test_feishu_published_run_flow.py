from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channels.feishu import FeishuChannel
from app.channels.manager import ChannelManager
from app.channels.message_bus import InboundMessage, MessageBus
from app.channels.published_runtime import (
    GatewayPublishedRunExecutor,
    PublishedChannelBusyError,
    PublishedChannelExecution,
    PublishedChannelRuntime,
    PublishedChannelUnavailableError,
    PublishedInboundPreparation,
    PublishedRunDetachedError,
)
from app.channels.store import ChannelStore, DbMappingStore
from deerflow.persistence.agent_channel import AgentChannelRow
from deerflow.persistence.base import Base
from deerflow.persistence.channel_mapping import ChannelEventRepository
from deerflow.persistence.published_agent import PublishedAgentRow
from deerflow.publishing.context import PublishedAgentContext
from deerflow.publishing.quota import EffectiveQuota, QuotaExceededError, Reservation
from deerflow.publishing.resolver import AgentNotAvailableError
from deerflow.runtime import DisconnectMode, MemoryStreamBridge, RunRecord, RunStatus


def _quota() -> EffectiveQuota:
    return EffectiveQuota(
        agent_max_concurrent_runs=2,
        agent_daily_runs=100,
        agent_daily_tokens=10_000,
        agent_inbound_rps=10,
        max_concurrent_runs=2,
        daily_runs=100,
        daily_tokens=10_000,
        max_run_seconds=30,
        max_tokens_per_run=2_000,
        max_input_bytes=4_096,
        inbound_rps=10,
    )


class _Resolver:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.calls: list[dict[str, object]] = []

    async def resolve(self, agent_id: str, **kwargs) -> PublishedAgentContext:
        self.order.append("resolve")
        self.calls.append({"agent_id": agent_id, **kwargs})
        return PublishedAgentContext(
            owner_user_id="owner-a",
            agent_id=agent_id,
            release_id="release-1",
            source="feishu",
            credential_id=str(kwargs["credential_id"]),
            external_actor=str(kwargs["external_actor"]),
            conversation_scope=str(kwargs["conversation_scope"]),
            skill_revision_ids=(),
            connector_capabilities=(),
            tool_groups=(),
            model_name="test-model",
            instructions="Published instructions",
            effective_quota=_quota(),
            correlation_id=str(kwargs["correlation_id"]),
            idempotency_key=None,
            memory_enabled=False,
        )


class _Ledger:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.settled_usage: list[dict[str, object]] = []
        self.released: list[str] = []

    async def reserve(self, context, *, request_key: str, run_id: str | None = None) -> Reservation:
        self.order.append("reserve")
        return Reservation(
            id="reservation-1",
            request_key=request_key,
            agent_id=context.agent_id,
            credential_id=context.credential_id,
            reserved_tokens=context.effective_quota.max_tokens_per_run,
            status="reserved",
        )

    async def settle(
        self,
        reservation_id: str,
        *,
        owner_user_id: str,
        tokens_used: int,
        status: str,
        run_id: str | None = None,
        usage=None,
    ) -> bool:
        self.order.append("settle")
        self.settled_usage.append(dict(usage or {}))
        return True

    async def release(self, reservation_id: str, *, owner_user_id: str) -> bool:
        self.released.append(reservation_id)
        return True

    async def release_unstarted(
        self,
        reservation_id: str,
        *,
        owner_user_id: str,
        run_id: str,
    ) -> bool:
        self.released.append(reservation_id)
        return True


class _Executor:
    def __init__(
        self,
        order: list[str],
        *,
        status: str = "success",
        text: str = "published answer",
    ) -> None:
        self.order = order
        self.calls: list[dict[str, object]] = []
        self.status = status
        self.text = text

    async def execute(self, **kwargs) -> PublishedChannelExecution:
        self.order.append("run")
        self.calls.append(kwargs)
        return PublishedChannelExecution(
            run_id=str(kwargs["run_id"]),
            thread_id=str(kwargs["thread_id"]),
            text=self.text,
            status=self.status,
            input_tokens=3,
            output_tokens=5,
            total_tokens=8,
            latency_ms=12,
        )


class _Mapping:
    async def get_or_create_thread(self, **kwargs) -> str:
        return "thread-1"


class _UnavailableResolver:
    async def resolve(self, agent_id: str, **kwargs) -> PublishedAgentContext:
        raise AgentNotAvailableError(agent_id)


class _QuotaExceededLedger(_Ledger):
    async def reserve(self, context, *, request_key: str, run_id: str | None = None) -> Reservation:
        self.order.append("reserve")
        raise QuotaExceededError("INBOUND_RPS_EXCEEDED", retry_after=1)


def _inbound(*, text: str = "hello") -> InboundMessage:
    return InboundMessage(
        channel_name="feishu:binding-1",
        chat_id="chat-1",
        user_id="user-1",
        text=text,
        metadata={
            "binding_id": "binding-1",
            "agent_id": "agent-1",
            "event_id": "event-1",
            "chat_type": "p2p",
        },
    )


async def _dispatch_once(runtime: PublishedChannelRuntime, store_path) -> str:
    bus = MessageBus()
    manager = ChannelManager(bus, ChannelStore(store_path), published_runtime=runtime)
    outbound = asyncio.get_running_loop().create_future()

    async def capture(message) -> None:
        if not outbound.done():
            outbound.set_result(message.text)

    bus.subscribe_outbound(capture)
    await manager.start()
    await bus.publish_inbound(_inbound())
    text = await asyncio.wait_for(outbound, timeout=2.0)
    await manager.stop()
    return str(text)


def _event(
    event_id: str = "event-1",
    *,
    text: str = "hello published agent",
    age_seconds: float = 0,
):
    return SimpleNamespace(
        header=SimpleNamespace(
            event_id=event_id,
            create_time=str(int((time.time() - age_seconds) * 1000)),
            token="verification-token",
        ),
        event=SimpleNamespace(
            message=SimpleNamespace(
                chat_id="chat-1",
                message_id="message-1",
                root_id=None,
                thread_id=None,
                chat_type="p2p",
                content=json.dumps({"text": text}),
            ),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="user-1")),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["hello published agent", "/memory"])
async def test_binding_message_uses_db_mapping_published_agent_quota_and_one_usage(
    tmp_path,
    text: str,
) -> None:
    database_path = tmp_path / "published-feishu.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path.as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            PublishedAgentRow(
                id="agent-1",
                owner_user_id="owner-a",
                slug="agent-one",
                display_name="Agent One",
                status="published",
            )
        )
        session.add(
            AgentChannelRow(
                id="binding-1",
                agent_id="agent-1",
                channel_type="feishu",
                app_id="app-one",
                secret_ref="secret://feishu/11111111111111111111111111111111",
                status="active",
            )
        )
        await session.commit()

    order: list[str] = []
    resolver = _Resolver(order)
    ledger = _Ledger(order)
    executor = _Executor(order)
    mappings = DbMappingStore(session_factory)
    runtime = PublishedChannelRuntime(
        mapping_store=mappings,
        resolver=resolver,
        quota_ledger=ledger,
        executor=executor,
    )
    bus = MessageBus()
    legacy_store_path = tmp_path / "legacy-store.json"
    manager = ChannelManager(bus, ChannelStore(legacy_store_path), published_runtime=runtime)
    outbound = asyncio.get_running_loop().create_future()

    async def capture(message) -> None:
        if not outbound.done():
            outbound.set_result(message)

    bus.subscribe_outbound(capture)
    await manager.start()
    channel = FeishuChannel(
        bus,
        app_id="app-one",
        app_secret="secret",
        verification_token="verification-token",
        binding_id="binding-1",
        agent_id="agent-1",
        event_deduplicator=ChannelEventRepository(session_factory),
    )
    channel._main_loop = asyncio.get_running_loop()

    channel._on_message(_event(text=text, age_seconds=1))
    channel._on_message(_event(text=text, age_seconds=1))
    response = await asyncio.wait_for(outbound, timeout=2.0)
    await asyncio.sleep(0.05)
    await manager.stop()

    assert response.text == "published answer"
    assert response.channel_name == "feishu:binding-1"
    assert order == ["resolve", "reserve", "run", "settle"]
    assert len(executor.calls) == 1
    assert len(ledger.settled_usage) == 1
    assert ledger.settled_usage[0]["source"] == "feishu"
    assert int(ledger.settled_usage[0]["event_latency_ms"]) >= 900
    assert ledger.settled_usage[0]["event_latency_ms"] != 12
    assert resolver.calls[0]["agent_id"] == "agent-1"
    assert resolver.calls[0]["credential_id"] == "binding-1"
    assert resolver.calls[0]["source"] == "feishu"
    assert executor.calls[0]["context"].memory_enabled is False
    assert executor.calls[0]["message"] == text
    rows = await mappings.list_mappings(binding_id="binding-1", owner_user_id="owner-a")
    assert len(rows) == 1
    assert rows[0].thread_id == resolver.calls[0]["conversation_scope"]
    assert legacy_store_path.exists() is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_quota_rejection_returns_safe_busy_message_without_run_or_usage(tmp_path) -> None:
    order: list[str] = []
    ledger = _QuotaExceededLedger(order)
    executor = _Executor(order)
    runtime = PublishedChannelRuntime(
        mapping_store=_Mapping(),
        resolver=_Resolver(order),
        quota_ledger=ledger,
        executor=executor,
    )

    text = await _dispatch_once(runtime, tmp_path / "quota-legacy.json")

    assert text == "This agent is busy. Please try again later."
    assert order == ["resolve", "reserve"]
    assert executor.calls == []
    assert ledger.settled_usage == []


@pytest.mark.asyncio
async def test_unpublished_agent_returns_safe_unavailable_message_before_quota(tmp_path) -> None:
    order: list[str] = []
    ledger = _Ledger(order)
    executor = _Executor(order)
    runtime = PublishedChannelRuntime(
        mapping_store=_Mapping(),
        resolver=_UnavailableResolver(),
        quota_ledger=ledger,
        executor=executor,
    )

    text = await _dispatch_once(runtime, tmp_path / "unavailable-legacy.json")

    assert text == "This agent is currently unavailable."
    assert order == []
    assert executor.calls == []
    assert ledger.settled_usage == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_at", ["unavailable", "quota"])
async def test_attachment_download_starts_only_after_release_and_quota_admission(reject_at: str) -> None:
    order: list[str] = []
    resolver = _UnavailableResolver() if reject_at == "unavailable" else _Resolver(order)
    ledger = _QuotaExceededLedger(order) if reject_at == "quota" else _Ledger(order)
    runtime = PublishedChannelRuntime(
        mapping_store=_Mapping(),
        resolver=resolver,
        quota_ledger=ledger,
        executor=_Executor(order),
    )
    inbound = _inbound(text="review [file]")
    inbound.thread_ts = "message-1"
    inbound.files = [{"file_key": "file-1", "filename": "input.txt"}]
    downloads: list[str] = []

    async def prepare(message, thread_id, owner_user_id, max_input_bytes):
        downloads.append("download")
        return PublishedInboundPreparation(message=message, attachment_bytes=1)

    expected_error = PublishedChannelUnavailableError if reject_at == "unavailable" else PublishedChannelBusyError
    with pytest.raises(expected_error):
        await runtime.run(inbound, prepare_inbound=prepare)

    assert downloads == []


@pytest.mark.asyncio
async def test_rejected_materialization_releases_only_the_unstarted_reservation() -> None:
    order: list[str] = []
    ledger = _Ledger(order)
    executor = _Executor(order)
    runtime = PublishedChannelRuntime(
        mapping_store=_Mapping(),
        resolver=_Resolver(order),
        quota_ledger=ledger,
        executor=executor,
    )

    async def reject_materialization(message, thread_id, owner_user_id, max_input_bytes):
        raise ValueError("aggregate attachment limit exceeded")

    with pytest.raises(PublishedChannelBusyError, match="attachment could not be admitted"):
        await runtime.run(_inbound(), prepare_inbound=reject_materialization)

    assert ledger.released == ["reservation-1"]
    assert ledger.settled_usage == []
    assert executor.calls == []


@pytest.mark.asyncio
async def test_timeout_returns_safe_message_and_settles_one_terminal_usage(tmp_path) -> None:
    order: list[str] = []
    ledger = _Ledger(order)
    executor = _Executor(
        order,
        status="timeout",
        text="The request timed out. Please try again.",
    )
    runtime = PublishedChannelRuntime(
        mapping_store=_Mapping(),
        resolver=_Resolver(order),
        quota_ledger=ledger,
        executor=executor,
    )

    text = await _dispatch_once(runtime, tmp_path / "timeout-legacy.json")

    assert text == "The request timed out. Please try again."
    assert order == ["resolve", "reserve", "run", "settle"]
    assert len(ledger.settled_usage) == 1
    assert ledger.settled_usage[0]["status"] == "timeout"


@pytest.mark.asyncio
async def test_gateway_executor_starts_memory_free_published_run_with_feishu_authority() -> None:
    context = await _Resolver([]).resolve(
        "agent-1",
        source="feishu",
        credential_id="binding-1",
        external_actor="feishu:user-1",
        conversation_scope="thread-1",
        correlation_id="event-1",
    )
    reservation = Reservation(
        id="reservation-1",
        request_key="request-1",
        agent_id="agent-1",
        credential_id="binding-1",
        reserved_tokens=2_000,
        status="reserved",
    )
    calls: list[dict[str, object]] = []

    async def start_run(body, thread_id, request, *, published_context=None, run_id=None) -> RunRecord:
        calls.append(
            {
                "body": body,
                "thread_id": thread_id,
                "request": request,
                "published_context": published_context,
                "run_id": run_id,
            }
        )
        record = RunRecord(
            run_id=str(run_id),
            thread_id=thread_id,
            assistant_id="lead_agent",
            status=RunStatus.success,
            on_disconnect=DisconnectMode.continue_,
        )
        record.last_ai_message = "gateway answer"
        record.total_input_tokens = 2
        record.total_output_tokens = 3
        record.total_tokens = 5
        record.task = asyncio.create_task(asyncio.sleep(0))
        return record

    executor = GatewayPublishedRunExecutor(FastAPI(), run_starter=start_run)
    result = await executor.execute(
        run_id="run-1",
        thread_id="thread-1",
        message="hello",
        context=context,
        reservation=reservation,
    )

    assert result.text == "gateway answer"
    assert result.total_tokens == 5
    assert calls[0]["published_context"] is context
    body = calls[0]["body"]
    assert body.metadata["published_agent"] is True
    assert body.metadata["published_source"] == "feishu"
    assert body.metadata["published_credential_id"] == "binding-1"
    assert body.metadata["published_quota_reservation_id"] == "reservation-1"


@pytest.mark.asyncio
async def test_gateway_executor_forwards_stream_progress_and_last_turn_artifacts() -> None:
    context = await _Resolver([]).resolve(
        "agent-1",
        source="feishu",
        credential_id="binding-1",
        external_actor="feishu:user-1",
        conversation_scope="thread-1",
        correlation_id="event-1",
    )
    reservation = Reservation(
        id="reservation-1",
        request_key="request-1",
        agent_id="agent-1",
        credential_id="binding-1",
        reserved_tokens=2_000,
        status="reserved",
    )
    bridge = MemoryStreamBridge()
    app = FastAPI()
    app.state.stream_bridge = bridge

    async def start_run(body, thread_id, request, *, published_context=None, run_id=None) -> RunRecord:
        record = RunRecord(
            run_id=str(run_id),
            thread_id=thread_id,
            assistant_id="lead_agent",
            status=RunStatus.running,
            on_disconnect=DisconnectMode.continue_,
        )

        async def worker() -> None:
            await bridge.publish(
                record.run_id,
                "messages-tuple",
                [{"type": "ai", "id": "answer-1", "content": "working"}, {}],
            )
            await bridge.publish(
                record.run_id,
                "values",
                {
                    "messages": [
                        {"type": "human", "content": "hello"},
                        {
                            "type": "ai",
                            "content": "gateway answer",
                            "tool_calls": [
                                {
                                    "name": "present_files",
                                    "args": {"filepaths": ["/mnt/user-data/outputs/report.txt"]},
                                }
                            ],
                        },
                    ]
                },
            )
            record.last_ai_message = "gateway answer"
            record.status = RunStatus.success
            await bridge.publish_end(record.run_id)

        record.task = asyncio.create_task(worker())
        return record

    progress: list[str] = []

    async def capture_progress(text: str) -> None:
        progress.append(text)

    result = await GatewayPublishedRunExecutor(app, run_starter=start_run).execute(
        run_id="run-stream",
        thread_id="thread-1",
        message="hello",
        context=context,
        reservation=reservation,
        on_progress=capture_progress,
    )

    assert progress == ["working"]
    assert result.artifacts == ("/mnt/user-data/outputs/report.txt",)


@pytest.mark.asyncio
async def test_slow_progress_delivery_never_blocks_final_stream_artifacts() -> None:
    context = await _Resolver([]).resolve(
        "agent-1",
        source="feishu",
        credential_id="binding-1",
        external_actor="feishu:user-1",
        conversation_scope="thread-1",
        correlation_id="event-1",
    )
    reservation = Reservation(
        id="reservation-1",
        request_key="request-1",
        agent_id="agent-1",
        credential_id="binding-1",
        reserved_tokens=2_000,
        status="reserved",
    )
    bridge = MemoryStreamBridge()
    app = FastAPI()
    app.state.stream_bridge = bridge

    async def start_run(body, thread_id, request, *, published_context=None, run_id=None) -> RunRecord:
        record = RunRecord(
            run_id=str(run_id),
            thread_id=thread_id,
            assistant_id="lead_agent",
            status=RunStatus.running,
            on_disconnect=DisconnectMode.continue_,
        )

        async def worker() -> None:
            await bridge.publish(
                record.run_id,
                "messages-tuple",
                [{"type": "ai", "id": "answer-1", "content": "working"}, {}],
            )
            await bridge.publish(
                record.run_id,
                "values",
                {
                    "messages": [
                        {"type": "human", "content": "hello"},
                        {
                            "type": "ai",
                            "content": "done",
                            "tool_calls": [
                                {
                                    "name": "present_files",
                                    "args": {"filepaths": ["/mnt/user-data/outputs/final.txt"]},
                                }
                            ],
                        },
                    ]
                },
            )
            record.last_ai_message = "done"
            record.status = RunStatus.success
            await bridge.publish_end(record.run_id)

        record.task = asyncio.create_task(worker())
        return record

    async def slow_progress(_text: str) -> None:
        await asyncio.sleep(2.0)

    started = time.perf_counter()
    result = await GatewayPublishedRunExecutor(app, run_starter=start_run).execute(
        run_id="run-slow-progress",
        thread_id="thread-1",
        message="hello",
        context=context,
        reservation=reservation,
        on_progress=slow_progress,
    )

    assert time.perf_counter() - started < 1.0
    assert result.text == "done"
    assert result.artifacts == ("/mnt/user-data/outputs/final.txt",)


@pytest.mark.asyncio
async def test_published_manager_emits_progress_and_resolves_final_attachment(tmp_path, monkeypatch) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    report_path = output_dir / "report.txt"
    report_path.write_text("report", encoding="utf-8")

    class _Paths:
        def sandbox_outputs_dir(self, thread_id: str, *, user_id: str):
            assert thread_id == "thread-1"
            assert user_id == "owner-a"
            return output_dir

        def resolve_virtual_path(self, thread_id: str, virtual_path: str, *, user_id: str):
            assert thread_id == "thread-1"
            assert virtual_path == "/mnt/user-data/outputs/report.txt"
            assert user_id == "owner-a"
            return report_path

    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: _Paths())

    class _StreamingRuntime:
        async def run(self, message, *, prepare_inbound=None, on_progress=None):
            assert on_progress is not None
            await on_progress("thread-1", "working")
            return PublishedChannelExecution(
                run_id="run-1",
                thread_id="thread-1",
                text="done",
                status="success",
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                latency_ms=1,
                artifacts=("/mnt/user-data/outputs/report.txt",),
                owner_user_id="owner-a",
            )

    bus = MessageBus()
    manager = ChannelManager(bus, ChannelStore(tmp_path / "legacy.json"), published_runtime=_StreamingRuntime())
    messages = []

    async def capture(message) -> None:
        messages.append(message)

    bus.subscribe_outbound(capture)
    await manager._handle_published_chat(_inbound())

    assert [message.is_final for message in messages] == [False, True]
    assert messages[0].text == "working"
    assert messages[-1].text.startswith("done\n\nCreated File:")
    assert "report.txt" in messages[-1].text
    assert messages[-1].artifacts == ["/mnt/user-data/outputs/report.txt"]
    assert [attachment.filename for attachment in messages[-1].attachments] == ["report.txt"]


@pytest.mark.asyncio
async def test_published_manager_prepares_dynamic_feishu_inbound_file_before_execution(tmp_path, monkeypatch) -> None:
    order: list[str] = []
    executor = _Executor(order)
    runtime = PublishedChannelRuntime(
        mapping_store=_Mapping(),
        resolver=_Resolver(order),
        quota_ledger=_Ledger(order),
        executor=executor,
    )

    class _DynamicChannel:
        async def receive_file(self, message, thread_id: str):
            assert thread_id == "thread-1"
            message.text = message.text.replace("[file]", "/mnt/user-data/uploads/input.txt")
            return message

    class _Service:
        def get_channel(self, name: str):
            assert name == "feishu:binding-1"
            return _DynamicChannel()

    monkeypatch.setattr("app.channels.service.get_channel_service", lambda: _Service())
    manager = ChannelManager(MessageBus(), ChannelStore(tmp_path / "legacy.json"), published_runtime=runtime)
    inbound = _inbound(text="review [file]")
    inbound.thread_ts = "message-1"
    inbound.files = [{"file_key": "file-1", "filename": "input.txt"}]

    await manager._handle_published_chat(inbound)

    assert executor.calls[0]["message"] == "review /mnt/user-data/uploads/input.txt"


@pytest.mark.asyncio
async def test_dispatcher_cancellation_cancels_started_run_and_settles_once() -> None:
    started = asyncio.Event()
    record_holder: list[RunRecord] = []

    async def start_run(body, thread_id, request, *, published_context=None, run_id=None) -> RunRecord:
        async def run_worker() -> None:
            started.set()
            await asyncio.Event().wait()

        record = RunRecord(
            run_id=str(run_id),
            thread_id=thread_id,
            assistant_id="lead_agent",
            status=RunStatus.running,
            on_disconnect=DisconnectMode.continue_,
        )
        record.task = asyncio.create_task(run_worker())
        record_holder.append(record)
        return record

    class _RunManager:
        async def cancel(self, run_id: str) -> bool:
            record = record_holder[0]
            assert record.run_id == run_id
            record.status = RunStatus.interrupted
            if record.task is not None:
                record.task.cancel()
            return True

    app = FastAPI()
    app.state.run_manager = _RunManager()
    order: list[str] = []
    ledger = _Ledger(order)
    runtime = PublishedChannelRuntime(
        mapping_store=_Mapping(),
        resolver=_Resolver(order),
        quota_ledger=ledger,
        executor=GatewayPublishedRunExecutor(app, run_starter=start_run),
    )

    dispatcher = asyncio.create_task(runtime.run(_inbound()))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    dispatcher.cancel()
    execution = await dispatcher

    assert execution.status == "cancelled"
    assert record_holder[0].task is not None and record_holder[0].task.done()
    assert ledger.released == []
    assert len(ledger.settled_usage) == 1
    assert ledger.settled_usage[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_dispatcher_cancellation_during_progress_drain_keeps_started_reservation_pending() -> None:
    progress_started = asyncio.Event()
    worker_finished = asyncio.Event()
    record_holder: list[RunRecord] = []
    bridge = MemoryStreamBridge()

    async def start_run(body, thread_id, request, *, published_context=None, run_id=None) -> RunRecord:
        record = RunRecord(
            run_id=str(run_id),
            thread_id=thread_id,
            assistant_id="lead_agent",
            status=RunStatus.running,
            on_disconnect=DisconnectMode.continue_,
        )

        async def run_worker() -> None:
            await bridge.publish(
                record.run_id,
                "messages-tuple",
                [{"type": "ai", "id": "answer-1", "content": "working"}, {}],
            )
            record.last_ai_message = "done"
            record.status = RunStatus.success
            await bridge.publish_end(record.run_id)
            worker_finished.set()

        record.task = asyncio.create_task(run_worker())
        record_holder.append(record)
        return record

    async def blocked_progress(_thread_id: str, _text: str) -> None:
        progress_started.set()
        await asyncio.Event().wait()

    app = FastAPI()
    app.state.stream_bridge = bridge
    order: list[str] = []
    ledger = _Ledger(order)
    runtime = PublishedChannelRuntime(
        mapping_store=_Mapping(),
        resolver=_Resolver(order),
        quota_ledger=ledger,
        executor=GatewayPublishedRunExecutor(app, run_starter=start_run),
    )

    dispatcher = asyncio.create_task(runtime.run(_inbound(), on_progress=blocked_progress))
    await asyncio.wait_for(progress_started.wait(), timeout=1.0)
    await asyncio.wait_for(worker_finished.wait(), timeout=1.0)
    assert record_holder[0].task is not None
    await asyncio.wait_for(asyncio.shield(record_holder[0].task), timeout=1.0)
    dispatcher.cancel()

    with pytest.raises(PublishedRunDetachedError, match="progress cleanup"):
        await asyncio.wait_for(dispatcher, timeout=1.0)

    assert ledger.released == []
    assert ledger.settled_usage == []


@pytest.mark.asyncio
async def test_timeout_cleanup_is_bounded_when_worker_suppresses_cancellation() -> None:
    record_holder: list[RunRecord] = []
    cancellation_suppressed = asyncio.Event()
    release_worker = asyncio.Event()

    class _ShortTimeoutResolver(_Resolver):
        async def resolve(self, agent_id: str, **kwargs) -> PublishedAgentContext:
            context = await super().resolve(agent_id, **kwargs)
            return replace(
                context,
                effective_quota=replace(context.effective_quota, max_run_seconds=0.01),
            )

    async def start_run(body, thread_id, request, *, published_context=None, run_id=None) -> RunRecord:
        async def run_worker() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_suppressed.set()
                await release_worker.wait()

        record = RunRecord(
            run_id=str(run_id),
            thread_id=thread_id,
            assistant_id="lead_agent",
            status=RunStatus.running,
            on_disconnect=DisconnectMode.continue_,
        )
        record.task = asyncio.create_task(run_worker())
        record_holder.append(record)
        return record

    class _RunManager:
        async def cancel(self, run_id: str) -> bool:
            record = record_holder[0]
            assert record.run_id == run_id
            assert record.task is not None
            record.task.cancel()
            return True

    app = FastAPI()
    app.state.run_manager = _RunManager()
    order: list[str] = []
    ledger = _Ledger(order)
    runtime = PublishedChannelRuntime(
        mapping_store=_Mapping(),
        resolver=_ShortTimeoutResolver(order),
        quota_ledger=ledger,
        executor=GatewayPublishedRunExecutor(
            app,
            run_starter=start_run,
            cleanup_timeout_seconds=0.05,
        ),
    )

    started_at = time.perf_counter()
    try:
        with pytest.raises(PublishedRunDetachedError, match="timeout cleanup failed"):
            await asyncio.wait_for(runtime.run(_inbound()), timeout=0.5)
        await asyncio.wait_for(cancellation_suppressed.wait(), timeout=0.5)
    finally:
        release_worker.set()
        worker = record_holder[0].task if record_holder else None
        if worker is not None:
            await asyncio.gather(worker, return_exceptions=True)

    assert time.perf_counter() - started_at < 0.5
    assert ledger.released == []
    assert ledger.settled_usage == []


@pytest.mark.asyncio
async def test_timeout_cancel_failure_keeps_started_run_reservation_pending() -> None:
    record_holder: list[RunRecord] = []

    class _ShortTimeoutResolver(_Resolver):
        async def resolve(self, agent_id: str, **kwargs) -> PublishedAgentContext:
            context = await super().resolve(agent_id, **kwargs)
            return replace(
                context,
                effective_quota=replace(context.effective_quota, max_run_seconds=0.01),
            )

    async def start_run(body, thread_id, request, *, published_context=None, run_id=None) -> RunRecord:
        async def run_worker() -> None:
            await asyncio.Event().wait()

        record = RunRecord(
            run_id=str(run_id),
            thread_id=thread_id,
            assistant_id="lead_agent",
            status=RunStatus.running,
            on_disconnect=DisconnectMode.continue_,
        )
        record.task = asyncio.create_task(run_worker())
        record_holder.append(record)
        return record

    class _FailingRunManager:
        async def cancel(self, run_id: str) -> bool:
            assert record_holder[0].run_id == run_id
            raise RuntimeError("persistence unavailable")

    app = FastAPI()
    app.state.run_manager = _FailingRunManager()
    order: list[str] = []
    ledger = _Ledger(order)
    runtime = PublishedChannelRuntime(
        mapping_store=_Mapping(),
        resolver=_ShortTimeoutResolver(order),
        quota_ledger=ledger,
        executor=GatewayPublishedRunExecutor(app, run_starter=start_run),
    )

    try:
        with pytest.raises(PublishedRunDetachedError, match="started Run timeout cleanup failed"):
            await runtime.run(_inbound())
    finally:
        worker = record_holder[0].task
        assert worker is not None
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    assert ledger.released == []
    assert ledger.settled_usage == []
