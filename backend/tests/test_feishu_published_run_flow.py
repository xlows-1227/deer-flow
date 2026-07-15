from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channels.feishu import FeishuChannel
from app.channels.manager import ChannelManager
from app.channels.message_bus import InboundMessage, MessageBus
from app.channels.published_runtime import (
    GatewayPublishedRunExecutor,
    PublishedChannelExecution,
    PublishedChannelRuntime,
)
from app.channels.store import ChannelStore, DbMappingStore
from deerflow.persistence.agent_channel import AgentChannelRow
from deerflow.persistence.base import Base
from deerflow.persistence.channel_mapping import ChannelEventRepository
from deerflow.persistence.published_agent import PublishedAgentRow
from deerflow.publishing.context import PublishedAgentContext
from deerflow.publishing.quota import EffectiveQuota, QuotaExceededError, Reservation
from deerflow.publishing.resolver import AgentNotAvailableError
from deerflow.runtime import DisconnectMode, RunRecord, RunStatus


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


def _event(event_id: str = "event-1", *, text: str = "hello published agent"):
    return SimpleNamespace(
        header=SimpleNamespace(
            event_id=event_id,
            create_time=str(int(time.time() * 1000)),
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

    channel._on_message(_event(text=text))
    channel._on_message(_event(text=text))
    response = await asyncio.wait_for(outbound, timeout=2.0)
    await asyncio.sleep(0.05)
    await manager.stop()

    assert response.text == "published answer"
    assert response.channel_name == "feishu:binding-1"
    assert order == ["resolve", "reserve", "run", "settle"]
    assert len(executor.calls) == 1
    assert len(ledger.settled_usage) == 1
    assert ledger.settled_usage[0]["source"] == "feishu"
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
