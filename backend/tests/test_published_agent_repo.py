"""Repository integration tests for published_agents / agent_drafts.

Backed by an in-memory SQLite database per the established
``test_connectors_repository.py`` pattern. These tests cover the F1.1
acceptance criteria: owner-scoped CRUD, cross-owner isolation, slug conflict
detection, optimistic-concurrency (revision) on draft updates.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import event
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
    assert draft["skill_selection_mode"] == "explicit"


@pytest.mark.asyncio
async def test_get_authoring_state_loads_owner_bundle_from_one_repository_read(agent_repo):
    pub, drafts = agent_repo
    agent = await pub.create_agent(
        owner_user_id="user-a",
        slug="snapshot",
        display_name="Snapshot",
        description="identity",
    )
    await drafts.replace_skills(
        agent["id"],
        owner_user_id="user-a",
        skills=[{"skill_name": "reporting", "source": "public"}],
    )
    await drafts.replace_connector_grants(
        agent["id"],
        owner_user_id="user-a",
        grants=[
            {
                "connector_instance_id": "conn-1",
                "capability": "database.query",
            }
        ],
    )

    state = await pub.get_authoring_state(
        owner_user_id="user-a",
        slug="snapshot",
    )
    assert state["agent"]["description"] == "identity"
    assert state["draft"]["skills"] == [{"skill_name": "reporting", "source": "public"}]
    assert state["draft"]["connector_grants"] == [
        {
            "connector_instance_id": "conn-1",
            "capability": "database.query",
        }
    ]
    assert await pub.get_authoring_state(owner_user_id="user-b", slug="snapshot") is None


@pytest.mark.asyncio
async def test_publish_snapshot_reads_draft_and_children_with_one_select(agent_repo):
    pub, drafts = agent_repo
    agent = await pub.create_agent(
        owner_user_id="user-a",
        slug="publish-snapshot",
        display_name="Publish snapshot",
    )
    await drafts.update_bundle(
        agent["id"],
        owner_user_id="user-a",
        revision=1,
        skills=[{"skill_name": "reporting", "source": "public"}],
        connector_grants=[{"connector_instance_id": "conn-1", "capability": "read"}],
    )
    engine = drafts._sf.kw["bind"]  # noqa: SLF001 - assert repository SQL boundary
    statements: list[str] = []

    def _record_select(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record_select)
    try:
        snapshot = await drafts.get_publish_snapshot(
            agent["id"],
            owner_user_id="user-a",
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record_select)

    assert snapshot is not None
    assert snapshot["skills"] == [{"skill_name": "reporting", "source": "public"}]
    assert snapshot["connector_grants"] == [{"connector_instance_id": "conn-1", "capability": "read"}]
    assert len(statements) == 1


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


@pytest.mark.asyncio
async def test_setup_authoring_bundle_rolls_back_identity_when_flush_fails(agent_repo):
    pub, _drafts = agent_repo

    with pytest.raises(IntegrityError):
        await pub.setup_authoring_bundle(
            owner_user_id="user-a",
            slug="atomic-setup",
            display_name="Atomic",
            description="desc",
            soul_markdown="# soul",
            skills=[
                {"skill_name": "duplicate", "source": "public"},
                {"skill_name": "duplicate", "source": "public"},
            ],
        )

    assert await pub.list_by_owner("user-a") == []


@pytest.mark.asyncio
async def test_import_authoring_bundle_commits_identity_draft_and_skills_once(agent_repo):
    pub, drafts = agent_repo

    saved = await pub.import_authoring_bundle(
        owner_user_id="user-a",
        slug="legacy",
        display_name="Legacy",
        description="imported",
        soul_markdown="# legacy soul",
        model_name="model-x",
        tool_groups=["web"],
        skills=[{"skill_name": "reporting", "source": "public"}],
        skill_selection_mode="explicit",
    )

    agent_id = saved["agent"]["id"]
    draft = await drafts.get(agent_id, owner_user_id="user-a")
    assert saved["agent"]["status"] == "draft"
    assert saved["agent"]["current_release_id"] is None
    assert draft["revision"] == 1
    assert draft["soul_markdown"] == "# legacy soul"
    assert draft["model_name"] == "model-x"
    assert draft["tool_groups"] == ["web"]
    assert draft["skills"] == [{"skill_name": "reporting", "source": "public"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_name", ["_after_import_draft_flush", "_after_import_skills_flush"])
async def test_import_authoring_bundle_rolls_back_every_phase_failure(agent_repo, hook_name):
    pub, _drafts = agent_repo

    async def _fail() -> None:
        raise RuntimeError("injected import failure")

    setattr(pub, hook_name, _fail)
    with pytest.raises(RuntimeError, match="injected import failure"):
        await pub.import_authoring_bundle(
            owner_user_id="user-a",
            slug="atomic-import",
            display_name="Atomic import",
            description=None,
            soul_markdown="# soul",
            model_name=None,
            tool_groups=[],
            skills=[{"skill_name": "reporting", "source": "public"}],
            skill_selection_mode="explicit",
        )

    assert await pub.list_by_owner("user-a") == []


@pytest.mark.asyncio
async def test_import_authoring_bundle_duplicate_skill_rolls_back_identity(agent_repo):
    pub, _drafts = agent_repo
    duplicate = {"skill_name": "reporting", "source": "public"}

    with pytest.raises(IntegrityError):
        await pub.import_authoring_bundle(
            owner_user_id="user-a",
            slug="duplicate-import",
            display_name="Duplicate import",
            description=None,
            soul_markdown="# soul",
            model_name=None,
            tool_groups=[],
            skills=[duplicate, duplicate],
            skill_selection_mode="explicit",
        )

    assert await pub.list_by_owner("user-a") == []


@pytest.mark.asyncio
async def test_update_authoring_bundle_is_one_revision_and_rolls_back_all_fields(agent_repo):
    pub, drafts = agent_repo
    created = await pub.setup_authoring_bundle(
        owner_user_id="user-a",
        slug="atomic-update",
        display_name="Atomic",
        description="old desc",
        soul_markdown="old soul",
        skills=[{"skill_name": "old-skill", "source": "public"}],
    )
    agent_id = created["agent"]["id"]

    with pytest.raises(IntegrityError):
        await pub.update_authoring_bundle(
            owner_user_id="user-a",
            slug="atomic-update",
            description="new desc",
            soul_markdown="new soul",
            model_name="new-model",
            tool_groups=["new-tools"],
            skills=[
                {"skill_name": "duplicate", "source": "public"},
                {"skill_name": "duplicate", "source": "public"},
            ],
        )

    agent = await pub.get(agent_id, owner_user_id="user-a")
    draft = await drafts.get(agent_id, owner_user_id="user-a")
    assert agent["description"] == "old desc"
    assert draft["soul_markdown"] == "old soul"
    assert draft["model_name"] is None
    assert draft["tool_groups"] == []
    assert draft["skills"] == [{"skill_name": "old-skill", "source": "public"}]
    assert draft["revision"] == 1


@pytest.mark.asyncio
async def test_update_authoring_bundle_commits_all_fields_once(agent_repo):
    pub, drafts = agent_repo
    created = await pub.setup_authoring_bundle(
        owner_user_id="user-a",
        slug="single-commit",
        display_name="Single",
        description="old",
        soul_markdown="old",
        skills=[],
    )
    agent_id = created["agent"]["id"]
    await drafts.replace_connector_grants(
        agent_id,
        owner_user_id="user-a",
        grants=[{"connector_instance_id": "conn-1", "capability": "read"}],
    )
    saved = await pub.update_authoring_bundle(
        owner_user_id="user-a",
        slug="single-commit",
        description="new",
        soul_markdown="new soul",
        model_name="model-x",
        tool_groups=["group-x"],
        skills=[{"skill_name": "skill-x", "source": "private"}],
    )
    agent = await pub.get(agent_id, owner_user_id="user-a")
    draft = await drafts.get(agent_id, owner_user_id="user-a")
    assert agent["description"] == "new"
    assert draft["soul_markdown"] == "new soul"
    assert draft["model_name"] == "model-x"
    assert draft["tool_groups"] == ["group-x"]
    assert draft["skills"] == [{"skill_name": "skill-x", "source": "private"}]
    assert saved["draft"]["connector_grants"] == [{"connector_instance_id": "conn-1", "capability": "read"}]
    assert draft["connector_grants"] == saved["draft"]["connector_grants"]
    assert draft["revision"] == 3


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
async def test_concurrent_draft_update_only_one_wins(agent_repo):
    """Rereview Critical-2: two concurrent updates that both read revision=N must
    not both succeed (lost update). The DB-level CAS UPDATE ensures only one
    transaction's WHERE revision=N matches a row.

    Uses two independent sessions sharing the same SQLite file so both can read
    revision=1 before either commits; only the first UPDATE bumps the row to
    revision=2, so the second UPDATE matches zero rows and returns None.
    """
    pub, drafts = agent_repo
    agent = await pub.create_agent(owner_user_id="user-a", slug="race", display_name="R")

    import asyncio

    both_ready = asyncio.Event()
    ready_count = 0
    ready_lock = asyncio.Lock()

    async def _barrier_before_cas() -> None:
        nonlocal ready_count
        async with ready_lock:
            ready_count += 1
            if ready_count == 2:
                both_ready.set()
        await both_ready.wait()

    drafts._before_cas = _barrier_before_cas  # noqa: SLF001

    async def _update(soul):
        return await drafts.update_with_revision(agent["id"], owner_user_id="user-a", revision=1, soul_markdown=soul)

    first, second = await asyncio.gather(_update("# first"), _update("# second"))
    assert ready_count == 2
    assert sum(result is not None for result in (first, second)) == 1
    winner = first or second
    final = await drafts.get(agent["id"], owner_user_id="user-a")
    assert final["soul_markdown"] == winner["soul_markdown"]
    assert final["revision"] == 2


@pytest.mark.asyncio
async def test_concurrent_draft_bundle_only_one_wins(agent_repo):
    """Rereview Critical-2: same lost-update guarantee for the bundle path."""
    pub, drafts = agent_repo
    agent = await pub.create_agent(owner_user_id="user-a", slug="race-bundle", display_name="RB")

    import asyncio

    both_ready = asyncio.Event()
    ready_count = 0
    ready_lock = asyncio.Lock()

    async def _barrier_before_cas() -> None:
        nonlocal ready_count
        async with ready_lock:
            ready_count += 1
            if ready_count == 2:
                both_ready.set()
        await both_ready.wait()

    drafts._before_cas = _barrier_before_cas  # noqa: SLF001

    async def _update(soul, skill):
        return await drafts.update_bundle(
            agent["id"],
            owner_user_id="user-a",
            revision=1,
            soul_markdown=soul,
            skills=[{"skill_name": skill, "source": "public"}],
        )

    first, second = await asyncio.gather(_update("# first", "s1"), _update("# second", "s2"))
    assert ready_count == 2
    assert sum(result is not None for result in (first, second)) == 1
    winner = first or second
    final = await drafts.get(agent["id"], owner_user_id="user-a")
    assert final["soul_markdown"] == winner["soul_markdown"]
    assert final["skills"] == winner["skills"]
    assert final["revision"] == 2


@pytest.mark.asyncio
async def test_replace_skills_and_connector_grants(agent_repo):
    pub, drafts = agent_repo
    agent = await pub.create_agent(owner_user_id="user-a", slug="skills", display_name="SK")

    first = await drafts.replace_skills(
        agent["id"],
        owner_user_id="user-a",
        skills=[{"skill_name": "reporting", "source": "public"}, {"skill_name": "private-x", "source": "private"}],
    )
    draft = await drafts.get(agent["id"], owner_user_id="user-a")
    assert {s["skill_name"] for s in draft["skills"]} == {"reporting", "private-x"}
    assert draft["skill_selection_mode"] == "explicit"
    assert first["revision"] == draft["revision"] == 2

    # Replace with a smaller set; old rows are removed.
    second = await drafts.replace_skills(
        agent["id"],
        owner_user_id="user-a",
        skills=[{"skill_name": "only-one", "source": "public"}],
    )
    draft = await drafts.get(agent["id"], owner_user_id="user-a")
    assert [s["skill_name"] for s in draft["skills"]] == ["only-one"]
    assert second["revision"] == draft["revision"] == 3

    # Cross-owner replace rejected.
    assert await drafts.replace_skills(agent["id"], owner_user_id="user-b", skills=[{"skill_name": "evil", "source": "public"}]) is None
    draft = await drafts.get(agent["id"], owner_user_id="user-a")
    assert [s["skill_name"] for s in draft["skills"]] == ["only-one"]

    # Connector grants follow the same replace semantics.
    granted = await drafts.replace_connector_grants(
        agent["id"],
        owner_user_id="user-a",
        grants=[{"connector_instance_id": "conn_1", "capability": "database.query"}],
    )
    draft = await drafts.get(agent["id"], owner_user_id="user-a")
    assert draft["connector_grants"] == [{"connector_instance_id": "conn_1", "capability": "database.query"}]
    assert granted["revision"] == draft["revision"] == 4

    assert await drafts.replace_connector_grants(agent["id"], owner_user_id="user-b", grants=[]) is None
    draft = await drafts.get(agent["id"], owner_user_id="user-a")
    assert draft["connector_grants"] == [{"connector_instance_id": "conn_1", "capability": "database.query"}]
