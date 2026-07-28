"""Repository tests for immutable agent_releases and the release sub-tables.

Covers F1.2 acceptance: the repository exposes no update method (verified by
introspection), ``release_no`` increments monotonically per agent without
gaps or duplicates, and cross-owner reads are rejected.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.agent_release import AgentReleaseRepository
from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import PublishedAgentRepository


@pytest_asyncio.fixture()
async def env(tmp_path):
    """Yield (release_repo, agent_repo) sharing one engine.

    Releases belong to agents, so ownership is resolved through the
    ``published_agents`` table; tests that need a release must seed the agent
    first via ``agent_repo.create_agent``.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'releases.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        sf = async_sessionmaker(engine, expire_on_commit=False)
        yield AgentReleaseRepository(sf), PublishedAgentRepository(sf)
    finally:
        await engine.dispose()


def _release_values(agent_id: str, release_no: int, *, checksum: str | None = None, **extra):
    base = {
        "id": f"rel_{agent_id}_{release_no}",
        "agent_id": agent_id,
        "release_no": release_no,
        "agent_markdown": "# Agent",
        "soul_markdown": "",
        "model_name": "gpt-x",
        "tool_groups": ["web"],
        "quota_overrides": {},
        "manifest_checksum": checksum or f"sha256:{agent_id}:{release_no}",
        "created_by": "user-a",
    }
    base.update(extra)
    return base


@pytest.mark.asyncio
async def test_repository_has_no_update_methods(env):
    """Immutability is enforced structurally: the repository surface must not expose any ``update`` / ``set`` / ``delete`` mutator on a release row."""
    release_repo, _ = env
    members = [m for m in dir(release_repo) if not m.startswith("_")]
    forbidden_prefixes = ("update", "set_", "delete", "modify", "patch", "edit")
    offending = [m for m in members if any(m.startswith(p) for p in forbidden_prefixes)]
    assert offending == [], f"repository exposes mutators on immutable releases: {offending}"
    # Must expose exactly the read/create surface the plan names.
    for required in ("create", "get", "list_by_agent", "get_by_release_no"):
        assert required in members, f"missing required method: {required}"


@pytest.mark.asyncio
async def test_release_no_monotonic_increment(env):
    release_repo, agent_repo = env
    agent = await agent_repo.create_agent(owner_user_id="user-a", slug="a", display_name="A")
    created = []
    for n in (1, 2, 3):
        rel = await release_repo.create(_release_values(agent["id"], n))
        created.append(rel)
    assert [r["release_no"] for r in created] == [1, 2, 3]
    history = await release_repo.list_by_agent(agent["id"], owner_user_id="user-a")
    assert [r["release_no"] for r in history] == [3, 2, 1]  # newest first


@pytest.mark.asyncio
async def test_release_no_next_helper(env):
    """The repository exposes a way to compute the next release_no without races."""
    release_repo, agent_repo = env
    agent = await agent_repo.create_agent(owner_user_id="user-a", slug="a", display_name="A")
    await release_repo.create(_release_values(agent["id"], 1))
    await release_repo.create(_release_values(agent["id"], 2))
    next_no = await release_repo.next_release_no(agent["id"])
    assert next_no == 3
    # A separate agent starts at 1.
    assert await release_repo.next_release_no("nonexistent-agent") == 1


@pytest.mark.asyncio
async def test_get_by_release_no(env):
    release_repo, agent_repo = env
    agent = await agent_repo.create_agent(owner_user_id="user-a", slug="a", display_name="A")
    await release_repo.create(_release_values(agent["id"], 1))
    await release_repo.create(_release_values(agent["id"], 2))
    rel = await release_repo.get_by_release_no(agent["id"], release_no=2, owner_user_id="user-a")
    assert rel is not None
    assert rel["release_no"] == 2
    # Cross-owner cannot read.
    assert await release_repo.get_by_release_no(agent["id"], release_no=2, owner_user_id="user-b") is None
    assert await release_repo.get_by_release_no(agent["id"], release_no=99, owner_user_id="user-a") is None


@pytest.mark.asyncio
async def test_create_with_skills_and_grants(env):
    release_repo, agent_repo = env
    agent = await agent_repo.create_agent(owner_user_id="user-a", slug="a", display_name="A")
    rel = await release_repo.create(
        _release_values(agent["id"], 1),
        skills=[{"skill_revision_id": "skr_1"}, {"skill_revision_id": "skr_2"}],
        connector_grants=[{"connector_instance_id": "conn_1", "capability": "database.query"}],
    )
    fetched = await release_repo.get(rel["id"], owner_user_id="user-a")
    assert {s["skill_revision_id"] for s in fetched["skills"]} == {"skr_1", "skr_2"}
    assert fetched["connector_grants"] == [{"connector_instance_id": "conn_1", "capability": "database.query"}]
    # Cross-owner get is rejected.
    assert await release_repo.get(rel["id"], owner_user_id="user-b") is None
