from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channels.feishu import FeishuChannel
from app.channels.message_bus import MessageBus
from deerflow.persistence.agent_channel.model import AgentChannelRow
from deerflow.persistence.base import Base
from deerflow.persistence.channel_mapping import (
    SYSTEM_CHANNEL_MAPPING_SCOPE,
    ChannelEventRepository,
    MappingScopeConflictError,
)
from deerflow.persistence.published_agent.model import PublishedAgentRow


def _event(
    *,
    event_id: str = "event-1",
    created_at: float | None = None,
    token: str = "verification-token",
):
    created_at = created_at if created_at is not None else time.time()
    return SimpleNamespace(
        header=SimpleNamespace(
            event_id=event_id,
            create_time=str(int(created_at * 1000)),
            token=token,
        ),
        event=SimpleNamespace(
            message=SimpleNamespace(
                chat_id="chat-1",
                message_id="message-1",
                root_id=None,
                thread_id=None,
                chat_type="p2p",
                content=json.dumps({"text": "hello"}),
            ),
            sender=SimpleNamespace(
                sender_id=SimpleNamespace(open_id="user-1"),
            ),
        ),
    )


@pytest_asyncio.fixture
async def event_repository(tmp_path):
    database_path = tmp_path / "channel-events.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path.as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                PublishedAgentRow(
                    id="agent-1",
                    owner_user_id="owner-1",
                    slug="agent-one",
                    display_name="Agent One",
                    status="published",
                ),
                AgentChannelRow(
                    id="binding-1",
                    agent_id="agent-1",
                    app_id="app-1",
                    secret_ref="secret-1",
                    status="active",
                ),
                AgentChannelRow(
                    id="binding-2",
                    agent_id="agent-1",
                    app_id="app-2",
                    secret_ref="secret-2",
                    status="inactive",
                ),
            ]
        )
        await session.commit()
    repository = ChannelEventRepository(session_factory)
    yield repository
    await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_event_is_dropped_before_bus_dispatch(event_repository: ChannelEventRepository) -> None:
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        event_deduplicator=event_repository,
        verification_token="verification-token",
    )
    channel._main_loop = asyncio.get_running_loop()

    channel._on_message(_event())
    channel._on_message(_event())
    await asyncio.sleep(0.1)

    assert bus.inbound_queue.qsize() == 1
    inbound = await bus.get_inbound()
    assert inbound.metadata["event_id"] == "event-1"
    assert inbound.metadata["binding_id"] == "binding-1"


@pytest.mark.asyncio
async def test_event_id_is_isolated_by_binding(event_repository: ChannelEventRepository) -> None:
    assert await event_repository.claim("binding-1", "event-1", system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE) is True
    assert await event_repository.claim("binding-1", "event-1", system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE) is False
    assert await event_repository.claim("binding-2", "event-1", system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE) is True


@pytest.mark.asyncio
async def test_event_claim_requires_system_scope_and_persisted_binding(event_repository: ChannelEventRepository) -> None:
    with pytest.raises(TypeError, match="system_scope"):
        await event_repository.claim("binding-1", "event-2")

    with pytest.raises(PermissionError, match="system channel mapping scope required"):
        await event_repository.claim("binding-1", "event-2", system_scope=object())

    with pytest.raises(MappingScopeConflictError, match="valid Feishu binding"):
        await event_repository.claim("forged-binding", "event-2", system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE)


@pytest.mark.asyncio
async def test_concurrent_event_claim_has_one_winner(event_repository: ChannelEventRepository) -> None:
    outcomes = await asyncio.gather(
        *(
            event_repository.claim(
                "binding-1",
                "event-concurrent",
                system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE,
            )
            for _ in range(4)
        )
    )

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 3


def test_tampered_verification_token_is_rejected_before_dispatch() -> None:
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        verification_token="verification-token",
    )
    channel._make_inbound = MagicMock()

    channel._on_message(_event(token="tampered-token"))

    channel._make_inbound.assert_not_called()
    assert bus.inbound_queue.empty()


def test_stale_timestamp_is_rejected_before_dispatch() -> None:
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        verification_token="verification-token",
    )
    channel._make_inbound = MagicMock()

    channel._on_message(_event(created_at=1_000.0))

    channel._make_inbound.assert_not_called()
    assert bus.inbound_queue.empty()
