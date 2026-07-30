from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import UniqueConstraint, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.agent_api_key import AgentAPIKeyRepository, AgentAPIKeyRow
from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import PublishedAgentRow


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 14, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


def test_agent_api_key_model_has_no_single_active_key_constraint() -> None:
    table = Base.metadata.tables["agent_api_keys"]
    assert {"agent_id", "secret_hash", "key_prefix", "quota_overrides_json", "rotation_of"} <= set(table.columns.keys())
    assert not any(isinstance(constraint, UniqueConstraint) for constraint in table.constraints)


@pytest_asyncio.fixture
async def key_repo():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                PublishedAgentRow(id="pa_1", owner_user_id="owner-a", slug="one", display_name="One"),
                PublishedAgentRow(id="pa_other", owner_user_id="owner-b", slug="other", display_name="Other"),
            ]
        )
        await session.commit()
    clock = _Clock()
    yield AgentAPIKeyRepository(session_factory, pepper="p" * 48, now_fn=clock.now), session_factory, clock
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_returns_plaintext_once_and_persists_only_slow_hash(key_repo) -> None:
    repo, session_factory, _clock = key_repo

    created = await repo.create(agent_id="pa_1", owner_user_id="owner-a", name="Production", quota_overrides={"daily_runs": 10})

    assert created["api_key"].startswith("dfa_")
    assert created["last_four"] == created["api_key"][-4:]
    async with session_factory() as session:
        row = (await session.execute(select(AgentAPIKeyRow))).scalar_one()
        assert row.secret_hash.startswith("scrypt$")
        assert row.secret_hash not in created["api_key"]
        assert created["api_key"] not in row.to_dict().values()

    listed = await repo.list_by_agent("pa_1", owner_user_id="owner-a")
    assert len(listed) == 1
    assert "api_key" not in listed[0]
    assert "secret_hash" not in listed[0]


@pytest.mark.asyncio
async def test_verify_revocation_and_multiple_active_keys(key_repo) -> None:
    repo, _session_factory, _clock = key_repo
    keys = [await repo.create(agent_id="pa_1", owner_user_id="owner-a", name=f"Key {index}") for index in range(3)]

    verified = [await repo.verify(item["api_key"]) for item in keys]
    assert all(item is not None for item in verified)
    assert len(await repo.list_by_agent("pa_1", owner_user_id="owner-a")) == 3

    assert await repo.revoke("pa_1", keys[1]["id"], owner_user_id="owner-a") is True
    assert await repo.verify(keys[1]["api_key"]) is None
    assert await repo.verify(keys[0]["api_key"]) is not None
    assert await repo.verify(keys[2]["api_key"]) is not None


@pytest.mark.asyncio
async def test_delete_permanently_removes_key_and_invalidates_plaintext(key_repo) -> None:
    repo, _session_factory, _clock = key_repo
    created = await repo.create(agent_id="pa_1", owner_user_id="owner-a", name="Temporary")

    assert await repo.delete("pa_1", created["id"], owner_user_id="owner-b") is False
    assert await repo.verify(created["api_key"]) is not None

    assert await repo.delete("pa_1", created["id"], owner_user_id="owner-a") is True
    assert await repo.get("pa_1", created["id"], owner_user_id="owner-a") is None
    assert await repo.verify(created["api_key"]) is None
    assert await repo.delete("pa_1", created["id"], owner_user_id="owner-a") is False


@pytest.mark.asyncio
async def test_rotation_keeps_old_key_during_overlap_then_expires_it(key_repo) -> None:
    repo, _session_factory, clock = key_repo
    old = await repo.create(agent_id="pa_1", owner_user_id="owner-a", name="Partner")

    rotated = await repo.rotate("pa_1", old["id"], owner_user_id="owner-a", overlap_seconds=3600)

    assert rotated["rotation_of"] == old["id"]
    assert await repo.verify(old["api_key"]) is not None
    assert await repo.verify(rotated["api_key"]) is not None

    clock.advance(seconds=3601)
    assert await repo.verify(old["api_key"]) is None
    assert await repo.verify(rotated["api_key"]) is not None
    statuses = {item["id"]: item["status"] for item in await repo.list_by_agent("pa_1", owner_user_id="owner-a")}
    assert statuses[old["id"]] == "expired"


@pytest.mark.asyncio
async def test_verify_rejects_malformed_unknown_and_tampered_keys(key_repo) -> None:
    repo, _session_factory, _clock = key_repo
    created = await repo.create(agent_id="pa_1", owner_user_id="owner-a", name="Key")

    assert await repo.verify("not-a-key") is None
    assert await repo.verify(created["api_key"][:-1] + "x") is None
    assert await repo.verify("dfa_" + "0" * 32 + "_" + "x" * 43) is None


@pytest.mark.asyncio
async def test_update_is_scoped_to_agent(key_repo) -> None:
    repo, _session_factory, _clock = key_repo
    created = await repo.create(agent_id="pa_1", owner_user_id="owner-a", name="Old")

    assert await repo.update("pa_1", created["id"], owner_user_id="owner-b", name="Stolen") is None
    updated = await repo.update("pa_1", created["id"], owner_user_id="owner-a", name="New", quota_overrides={"daily_runs": 5})

    assert updated is not None
    assert updated["name"] == "New"
    assert updated["quota_overrides"] == {"daily_runs": 5}


@pytest.mark.asyncio
async def test_management_reads_are_owner_scoped(key_repo) -> None:
    repo, _session_factory, _clock = key_repo
    created = await repo.create(agent_id="pa_1", owner_user_id="owner-a", name="Scoped")

    assert await repo.list_by_agent("pa_1", owner_user_id="owner-b") == []
    assert await repo.get("pa_1", created["id"], owner_user_id="owner-b") is None
    assert await repo.rotate("pa_1", created["id"], owner_user_id="owner-b") is None
    assert await repo.revoke("pa_1", created["id"], owner_user_id="owner-b") is False
