"""Repository integration tests for published_agents / agent_drafts.

Backed by an in-memory SQLite database per the established
``test_connectors_repository.py`` pattern. These tests cover the F1.1
acceptance criteria: owner-scoped CRUD, cross-owner isolation, slug conflict
detection, optimistic-concurrency (revision) on draft updates.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import (
    AgentDraftRepository,
    PublishedAgentRepository,
)


@pytest_asyncio.fixture()
async def agent_repo(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agents.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        sf = async_sessionmaker(engine, expire_on_commit=False)
        yield PublishedAgentRepository(sf), AgentDraftRepository(sf)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# PublishedAgentRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_agent_returns_identity_and_draft(agent_repo):
    pub, drafts = agent_repo
    agent = await pub.create_agent(
        owner_user_id="user-a",
        slug="sales-bot",
        display_name="Sales Bot",
        description="Closes deals",
    )
    assert agent["id"]
    assert agent["owner_user_id"] == "user-a"
    assert agent["slug"] == "sales-bot"
    assert agent["status"] == "draft"
    assert agent["current_release_id"] is None
    # Creating an agent seeds an empty draft (1:1).
    draft = await drafts.get(agent["id"], owner_user_id="user-a")
    assert draft is not None
    assert draft["revision"] == 1
    assert draft["agent_markdown"] == ""
    assert draft["soul_markdown"] == ""
    assert draft["tool_groups"] == []
    assert draft["quota_overrides"] == {}


@pytest.mark.asyncio
async def test_different_owners_can_share_same_slug(agent_repo):
    pub, _ = agent_repo
    a = await pub.create_agent(owner_user_id="user-a", slug="helper", display_name="A Helper")
    b = await pub.create_agent(owner_user_id="user-b", slug="helper", display_name="B Helper")
    assert a["id"] != b["id"]
    assert a["owner_user_id"] == "user-b" and b["owner_user_id"] == "user-b" or True
    assert a["owner_user_id"] == "user-a"
    assert b["owner_user_id"] == "user-b"


@pytest.mark.asyncio
async def test_duplicate_slug_within_owner_raises(agent_repo):
    pub, _ = agent_repo
    await pub.create_agent(owner_user_id="user-a", slug="dup", display_name="First")
    with pytest.raises((IntegrityError, ValueError)):
        await pub.create_agent(owner_user_id="user-a", slug="dup", display_name="Second")


@pytest.mark.asyncio
async def test_get_returns_none_for_other_owner(agent_repo):
    pub, _ = agent_repo
    created = await pub.create_agent(owner_user_id="user-a", slug="mine", display_name="Mine")
    assert await pub.get(created["id"], owner_user_id="user-b") is None
    assert (await pub.get(created["id"], owner_user_id="user-a"))["slug"] == "mine"


@pytest.mark.asyncio
async def test_list_by_owner_only_returns_own_agents(agent_repo):
    pub, _ = agent_repo
    await pub.create_agent(owner_user_id="user-a", slug="a1", display_name="A1")
    await pub.create_agent(owner_user_id="user-a", slug="a2", display_name="A2")
    await pub.create_agent(owner_user_id="user-b", slug="b1", display_name="B1")
    a_agents = await pub.list_by_owner("user-a")
    assert {a["slug"] for a in a_agents} == {"a1", "a2"}
    b_agents = await pub.list_by_owner("user-b")
    assert {a["slug"] for a in b_agents} == {"b1"}


@pytest.mark.asyncio
async def test_update_meta_owner_scoped(agent_repo):
    pub, _ = agent_repo
    created = await pub.create_agent(owner_user_id="user-a", slug="meta", display_name="Old")
    updated = await pub.update_meta(
        created["id"],
        owner_user_id="user-a",
        display_name="New",
        description="updated",
    )
    assert updated["display_name"] == "New"
    assert updated["description"] == "updated"
    # Cross-owner update is a no-op.
    assert await pub.update_meta(created["id"], owner_user_id="user-b", display_name="Hijack") is None
    assert (await pub.get(created["id"], owner_user_id="user-a"))["display_name"] == "New"


@pytest.mark.asyncio
async def test_set_status_and_set_current_release_owner_scoped(agent_repo):
    pub, _ = agent_repo
    created = await pub.create_agent(owner_user_id="user-a", slug="status", display_name="S")
    ok = await pub.set_status(created["id"], owner_user_id="user-a", status="suspended")
    assert ok is True
    assert (await pub.get(created["id"], owner_user_id="user-a"))["status"] == "suspended"
    # Cross-owner status change rejected (returns False, never mutates).
    assert await pub.set_status(created["id"], owner_user_id="user-b", status="archived") is False
    assert (await pub.get(created["id"], owner_user_id="user-a"))["status"] == "suspended"

    ok = await pub.set_current_release(created["id"], owner_user_id="user-a", release_id="rel_1")
    assert ok is True
    assert (await pub.get(created["id"], owner_user_id="user-a"))["current_release_id"] == "rel_1"
    assert await pub.set_current_release(created["id"], owner_user_id="user-b", release_id="rel_2") is False


# ---------------------------------------------------------------------------
# AgentDraftRepository — optimistic concurrency & sub-table replacement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_update_requires_matching_revision(agent_repo):
    pub, drafts = agent_repo
    agent = await pub.create_agent(owner_user_id="user-a", slug="rev", display_name="Rev")
    draft = await drafts.get(agent["id"], owner_user_id="user-a")
    assert draft["revision"] == 1

    updated = await drafts.update_with_revision(
        agent["id"],
        owner_user_id="user-a",
        revision=1,
        soul_markdown="# I am Rev",
    )
    assert updated["revision"] == 2
    assert updated["soul_markdown"] == "# I am Rev"

    # Stale revision (1 again) must be rejected.
    stale = await drafts.update_with_revision(
        agent["id"],
        owner_user_id="user-a",
        revision=1,
        soul_markdown="# overwrite",
    )
    assert stale is None
    # The content was not overwritten.
    again = await drafts.get(agent["id"], owner_user_id="user-a")
    assert again["revision"] == 2
    assert again["soul_markdown"] == "# I am Rev"


@pytest.mark.asyncio
async def test_draft_update_cross_owner_rejected(agent_repo):
    pub, drafts = agent_repo
    agent = await pub.create_agent(owner_user_id="user-a", slug="x", display_name="X")
    assert (
        await drafts.update_with_revision(
            agent["id"],
            owner_user_id="user-b",
            revision=1,
            soul_markdown="hijack",
        )
    ) is None
    assert (await drafts.get(agent["id"], owner_user_id="user-a"))["soul_markdown"] == ""


@pytest.mark.asyncio
async def test_draft_partial_update_keeps_other_fields(agent_repo):
    pub, drafts = agent_repo
    agent = await pub.create_agent(owner_user_id="user-a", slug="partial", display_name="P")
    await drafts.update_with_revision(agent["id"], owner_user_id="user-a", revision=1, agent_markdown="# Agent", model_name="gpt-x")
    # Update only soul_markdown; agent_markdown/model_name must persist.
    await drafts.update_with_revision(agent["id"], owner_user_id="user-a", revision=2, soul_markdown="# Soul")
    draft = await drafts.get(agent["id"], owner_user_id="user-a")
    assert draft["agent_markdown"] == "# Agent"
    assert draft["model_name"] == "gpt-x"
    assert draft["soul_markdown"] == "# Soul"


@pytest.mark.asyncio
async def test_replace_skills_and_connector_grants(agent_repo):
    pub, drafts = agent_repo
    agent = await pub.create_agent(owner_user_id="user-a", slug="skills", display_name="SK")

    await drafts.replace_skills(
        agent["id"],
        owner_user_id="user-a",
        skills=[{"skill_name": "reporting", "source": "public"}, {"skill_name": "private-x", "source": "private"}],
    )
    draft = await drafts.get(agent["id"], owner_user_id="user-a")
    assert {s["skill_name"] for s in draft["skills"]} == {"reporting", "private-x"}

    # Replace with a smaller set; old rows are removed.
    await drafts.replace_skills(
        agent["id"],
        owner_user_id="user-a",
        skills=[{"skill_name": "only-one", "source": "public"}],
    )
    draft = await drafts.get(agent["id"], owner_user_id="user-a")
    assert [s["skill_name"] for s in draft["skills"]] == ["only-one"]

    # Cross-owner replace rejected.
    assert await drafts.replace_skills(agent["id"], owner_user_id="user-b", skills=[{"skill_name": "evil", "source": "public"}]) is None
    draft = await drafts.get(agent["id"], owner_user_id="user-a")
    assert [s["skill_name"] for s in draft["skills"]] == ["only-one"]

    # Connector grants follow the same replace semantics.
    await drafts.replace_connector_grants(
        agent["id"],
        owner_user_id="user-a",
        grants=[{"connector_instance_id": "conn_1", "capability": "database.query"}],
    )
    draft = await drafts.get(agent["id"], owner_user_id="user-a")
    assert draft["connector_grants"] == [{"connector_instance_id": "conn_1", "capability": "database.query"}]

    assert await drafts.replace_connector_grants(agent["id"], owner_user_id="user-b", grants=[]) is None
    draft = await drafts.get(agent["id"], owner_user_id="user-a")
    assert draft["connector_grants"] == [{"connector_instance_id": "conn_1", "capability": "database.query"}]
