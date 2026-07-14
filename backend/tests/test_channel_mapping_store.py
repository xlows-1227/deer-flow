from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channels.store import DbMappingStore
from deerflow.persistence.base import Base
from deerflow.persistence.channel_mapping import ChannelConversationMappingRow, MappingScopeConflictError


@pytest_asyncio.fixture
async def mapping_store(tmp_path):
    database_path = tmp_path / "channel-mappings.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path.as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = DbMappingStore(async_sessionmaker(engine, expire_on_commit=False))
    yield store
    await engine.dispose()


@pytest.mark.asyncio
async def test_private_chat_isolated_by_feishu_user(mapping_store: DbMappingStore) -> None:
    first = await mapping_store.get_or_create_thread(
        binding_id="binding-1",
        agent_id="agent-1",
        chat_id="chat-1",
        feishu_user_id="user-a",
        chat_type="p2p",
    )
    second = await mapping_store.get_or_create_thread(
        binding_id="binding-1",
        agent_id="agent-1",
        chat_id="chat-1",
        feishu_user_id="user-b",
        chat_type="p2p",
    )

    assert first != second


@pytest.mark.asyncio
async def test_group_members_share_thread(mapping_store: DbMappingStore) -> None:
    first = await mapping_store.get_or_create_thread(
        binding_id="binding-1",
        agent_id="agent-1",
        chat_id="group-1",
        feishu_user_id="user-a",
        chat_type="group",
    )
    second = await mapping_store.get_or_create_thread(
        binding_id="binding-1",
        agent_id="agent-1",
        chat_id="group-1",
        feishu_user_id="user-b",
        chat_type="group",
    )

    assert first == second


@pytest.mark.asyncio
async def test_group_topics_are_isolated(mapping_store: DbMappingStore) -> None:
    first = await mapping_store.get_or_create_thread(
        binding_id="binding-1",
        agent_id="agent-1",
        chat_id="group-1",
        feishu_user_id="user-a",
        chat_type="group",
        topic_id="topic-a",
    )
    second = await mapping_store.get_or_create_thread(
        binding_id="binding-1",
        agent_id="agent-1",
        chat_id="group-1",
        feishu_user_id="user-b",
        chat_type="group",
        topic_id="topic-b",
    )

    assert first != second


@pytest.mark.asyncio
async def test_same_chat_isolated_across_bindings(mapping_store: DbMappingStore) -> None:
    first = await mapping_store.get_or_create_thread(
        binding_id="binding-1",
        agent_id="agent-1",
        chat_id="chat-1",
        feishu_user_id="user-a",
        chat_type="p2p",
    )
    second = await mapping_store.get_or_create_thread(
        binding_id="binding-2",
        agent_id="agent-2",
        chat_id="chat-1",
        feishu_user_id="user-a",
        chat_type="p2p",
    )

    assert first != second


@pytest.mark.asyncio
async def test_existing_binding_mapping_cannot_be_reassigned_to_another_agent(mapping_store: DbMappingStore) -> None:
    await mapping_store.get_or_create_thread(
        binding_id="binding-1",
        agent_id="agent-1",
        chat_id="chat-1",
        feishu_user_id="user-a",
        chat_type="p2p",
    )

    with pytest.raises(MappingScopeConflictError):
        await mapping_store.get_or_create_thread(
            binding_id="binding-1",
            agent_id="agent-2",
            chat_id="chat-1",
            feishu_user_id="user-a",
            chat_type="p2p",
        )


@pytest.mark.asyncio
async def test_concurrent_get_or_create_returns_one_mapping(mapping_store: DbMappingStore) -> None:
    async def resolve() -> str:
        return await mapping_store.get_or_create_thread(
            binding_id="binding-1",
            agent_id="agent-1",
            chat_id="chat-1",
            feishu_user_id="user-a",
            chat_type="p2p",
        )

    thread_ids = await asyncio.gather(*(resolve() for _ in range(12)))
    rows = await mapping_store.list_mappings(binding_id="binding-1")

    assert len(set(thread_ids)) == 1
    assert len(rows) == 1
    assert isinstance(rows[0], ChannelConversationMappingRow)
