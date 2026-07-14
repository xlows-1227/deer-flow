from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channels.feishu import FeishuChannel, FeishuEventVerifier
from app.channels.message_bus import MessageBus
from deerflow.persistence.base import Base
from deerflow.persistence.channel_mapping import ChannelEventRepository


def _event(*, event_id: str = "event-1", created_at: float | None = None):
    created_at = created_at if created_at is not None else time.time()
    return SimpleNamespace(
        header=SimpleNamespace(
            event_id=event_id,
            create_time=str(int(created_at * 1000)),
            token="verification-token",
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
    repository = ChannelEventRepository(async_sessionmaker(engine, expire_on_commit=False))
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
        event_verifier=FeishuEventVerifier(verification_token="verification-token"),
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
    assert await event_repository.claim("binding-1", "event-1") is True
    assert await event_repository.claim("binding-1", "event-1") is False
    assert await event_repository.claim("binding-2", "event-1") is True


def test_invalid_signature_is_rejected_before_dispatch() -> None:
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        event_verifier=lambda _event: False,
    )
    channel._make_inbound = MagicMock()

    channel._on_message(_event())

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
        event_verifier=FeishuEventVerifier(
            verification_token="verification-token",
            max_age_seconds=300,
            clock=lambda: 2_000.0,
        ),
    )
    channel._make_inbound = MagicMock()

    channel._on_message(_event(created_at=1_000.0))

    channel._make_inbound.assert_not_called()
    assert bus.inbound_queue.empty()
