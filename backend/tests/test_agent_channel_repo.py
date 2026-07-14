from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.agent_channel import (
    SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
    ActiveAgentChannelConflictError,
    AgentChannelRepository,
    AgentChannelRow,
)
from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import PublishedAgentRow


@pytest_asyncio.fixture
async def channel_repo():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                PublishedAgentRow(id="pa_1", owner_user_id="owner-a", slug="one", display_name="One", status="published"),
                PublishedAgentRow(id="pa_2", owner_user_id="owner-b", slug="two", display_name="Two", status="published"),
            ]
        )
        await session.commit()
    yield AgentChannelRepository(session_factory)
    await engine.dispose()


def test_agent_channel_model_contains_only_secret_reference() -> None:
    table = Base.metadata.tables[AgentChannelRow.__tablename__]

    assert {
        "id",
        "agent_id",
        "channel_type",
        "app_id",
        "secret_ref",
        "connection_mode",
        "status",
        "health",
        "health_detail",
        "created_at",
        "updated_at",
        "last_started_at",
    } <= set(table.columns.keys())
    assert not ({"app_secret", "secret", "token", "encrypt_key"} & set(table.columns.keys()))
    assert "uq_agent_channels_active" in {index.name for index in table.indexes}


@pytest.mark.asyncio
async def test_agent_channel_crud_is_owner_scoped(channel_repo) -> None:
    created = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_a",
        secret_ref="secret://feishu/11111111111111111111111111111111",
    )

    assert created is not None
    assert created["status"] == "inactive"
    assert created["health"] == "unknown"
    assert "app_secret" not in created
    assert await channel_repo.get("pa_1", created["id"], owner_user_id="owner-b") is None
    assert await channel_repo.list_by_agent("pa_1", owner_user_id="owner-b") == []
    assert (
        await channel_repo.update_credentials(
            "pa_1",
            created["id"],
            owner_user_id="owner-b",
            app_id="stolen",
            secret_ref="secret://feishu/22222222222222222222222222222222",
        )
        is None
    )

    updated = await channel_repo.update_credentials(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        app_id="cli_rotated",
        secret_ref="secret://feishu/33333333333333333333333333333333",
    )
    assert updated is not None
    assert updated["app_id"] == "cli_rotated"


@pytest.mark.asyncio
async def test_only_one_active_feishu_binding_per_agent(channel_repo) -> None:
    first = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_first",
        secret_ref="secret://feishu/11111111111111111111111111111111",
    )
    second = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_second",
        secret_ref="secret://feishu/22222222222222222222222222222222",
    )

    active = await channel_repo.activate("pa_1", first["id"], owner_user_id="owner-a")
    assert active is not None
    assert active["status"] == "active"
    with pytest.raises(ActiveAgentChannelConflictError):
        await channel_repo.activate("pa_1", second["id"], owner_user_id="owner-a")

    stopped = await channel_repo.deactivate("pa_1", first["id"], owner_user_id="owner-a")
    assert stopped is not None
    replacement = await channel_repo.activate("pa_1", second["id"], owner_user_id="owner-a")
    assert replacement is not None
    assert replacement["status"] == "active"


@pytest.mark.asyncio
async def test_supervisor_active_scan_requires_explicit_system_scope(channel_repo) -> None:
    first = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_first",
        secret_ref="secret://feishu/11111111111111111111111111111111",
    )
    other = await channel_repo.create(
        agent_id="pa_2",
        owner_user_id="owner-b",
        app_id="cli_other",
        secret_ref="secret://feishu/22222222222222222222222222222222",
    )
    await channel_repo.activate("pa_1", first["id"], owner_user_id="owner-a")
    await channel_repo.activate("pa_2", other["id"], owner_user_id="owner-b")

    with pytest.raises(PermissionError):
        await channel_repo.list_active(supervisor_scope=object())
    active = await channel_repo.list_active(supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE)

    assert {(row["owner_user_id"], row["agent_id"]) for row in active} == {
        ("owner-a", "pa_1"),
        ("owner-b", "pa_2"),
    }
