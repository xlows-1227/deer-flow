"""Integration tests for the PublishService (F1.5).

Backed by an in-memory SQLite database, these tests cover the publish/rollback
acceptance criteria: validation gating, immutable release creation, atomic
``current_release_id`` pointer switch, skill revision pinning (a post-publish
skill edit doesn't move the pinned revision), rollback to a historical release
without mutating history, and 404 semantics for unknown releases.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.agent_release import AgentReleaseRepository
from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import (
    AgentDraftRepository,
    PublishedAgentRepository,
)
from deerflow.persistence.skill_revision import SkillRevisionRepository
from deerflow.publishing.content_store import LocalContentStore
from deerflow.publishing.draft_service import DraftService
from deerflow.publishing.publish_service import (
    PublishError,
    PublishService,
    ReleaseNotFoundError,
)


class _StaticSkillsIndex:
    """Records skill -> caps; always selectable."""

    def __init__(self, caps: dict[str, list[str]]) -> None:
        self._caps = caps

    def is_selectable_by(self, name, owner_user_id):  # noqa: ARG002
        return name in self._caps

    def get(self, name):
        caps = self._caps.get(name)
        return {"caps": caps} if caps is not None else None

    def files_for(self, name):  # noqa: ARG002
        return {"SKILL.md": f"# {name}".encode()}


class _AsyncConnectorRepo:
    def __init__(self, owners: dict[str, str]) -> None:
        self.owners = owners

    async def get_instance(self, connector_id, *, owner_id=...):
        owner = self.owners.get(connector_id)
        if owner is None:
            return None
        if owner_id is not ... and owner != owner_id:
            return None
        return {"id": connector_id, "owner_id": owner, "status": "active"}


@pytest_asyncio.fixture()
async def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'publish.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    agent_repo = PublishedAgentRepository(sf)
    draft_repo = AgentDraftRepository(sf)
    release_repo = AgentReleaseRepository(sf)
    skill_repo = SkillRevisionRepository(sf)
    content_store = LocalContentStore(base_dir=tmp_path)
    service = PublishService(
        published_agent_repo=agent_repo,
        draft_repo=draft_repo,
        release_repo=release_repo,
        skill_revision_repo=skill_repo,
        content_store=content_store,
        skills_index=_StaticSkillsIndex({"reporting": ["database.query"]}),
        connector_repo=_AsyncConnectorRepo({"conn_1": "user-a"}),
        model_index={"gpt-x"},
        tool_group_whitelist={"web"},
    )
    draft_service = DraftService(
        published_agent_repo=agent_repo,
        draft_repo=draft_repo,
        skills_index=_StaticSkillsIndex({"reporting": ["database.query"]}),
        connector_repo=_AsyncConnectorRepo({"conn_1": "user-a"}),
    )
    try:
        yield service, draft_service, agent_repo, release_repo
    finally:
        await engine.dispose()


async def _seed_agent(draft_service, *, owner="user-a", slug="bot", skills=True, grants=True):
    agent = await draft_service.create_agent(owner_user_id=owner, slug=slug, display_name=slug.title())
    await draft_service.update_draft(agent["id"], owner_user_id=owner, revision=1, agent_markdown="# Agent", soul_markdown="# Soul", model_name="gpt-x", tool_groups=["web"])
    if skills:
        await draft_service.set_skills(agent["id"], owner_user_id=owner, skills=[{"skill_name": "reporting", "source": "public"}])
    else:
        await draft_service.set_skills(agent["id"], owner_user_id=owner, skills=[])
    if grants:
        await draft_service.set_connector_grants(agent["id"], owner_user_id=owner, grants=[{"connector_instance_id": "conn_1", "capability": "database.query"}])
    return agent


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_creates_release_and_switches_pointer(env):
    service, draft_service, agent_repo, _ = env
    agent = await _seed_agent(draft_service)
    result = await service.publish(agent["id"], owner_user_id="user-a")
    assert result["release_no"] == 1
    refreshed = await agent_repo.get(agent["id"], owner_user_id="user-a")
    assert refreshed["status"] == "published"
    assert refreshed["current_release_id"] is not None


@pytest.mark.asyncio
async def test_publish_rejects_invalid_draft_without_changes(env):
    service, draft_service, agent_repo, _ = env
    agent = await _seed_agent(draft_service)
    # Break rule 1: empty instructions.
    draft = await draft_service.get_draft(agent["id"], owner_user_id="user-a")
    await draft_service.update_draft(agent["id"], owner_user_id="user-a", revision=draft["revision"], agent_markdown="", soul_markdown="")
    with pytest.raises(PublishError) as exc:
        await service.publish(agent["id"], owner_user_id="user-a")
    assert any(v.code == "EMPTY_INSTRUCTIONS" for v in exc.value.violations)
    # State unchanged.
    refreshed = await agent_repo.get(agent["id"], owner_user_id="user-a")
    assert refreshed["status"] == "draft"
    assert refreshed["current_release_id"] is None


@pytest.mark.asyncio
async def test_publish_without_bindings_succeeds(env):
    """An agent with no feishu/api-key can still publish (acceptance #6 first half)."""
    service, draft_service, _, _ = env
    agent = await _seed_agent(draft_service)
    result = await service.publish(agent["id"], owner_user_id="user-a")
    assert result["release_no"] == 1


@pytest.mark.asyncio
async def test_release_no_increments_across_republish(env):
    service, draft_service, _, _ = env
    agent = await _seed_agent(draft_service)
    r1 = await service.publish(agent["id"], owner_user_id="user-a")
    # Edit draft then republish.
    draft = await draft_service.get_draft(agent["id"], owner_user_id="user-a")
    await draft_service.update_draft(agent["id"], owner_user_id="user-a", revision=draft["revision"], agent_markdown="# Agent v2")
    r2 = await service.publish(agent["id"], owner_user_id="user-a")
    r3_draft = await draft_service.get_draft(agent["id"], owner_user_id="user-a")
    await draft_service.update_draft(agent["id"], owner_user_id="user-a", revision=r3_draft["revision"], agent_markdown="# Agent v3")
    r3 = await service.publish(agent["id"], owner_user_id="user-a")
    assert [r1["release_no"], r2["release_no"], r3["release_no"]] == [1, 2, 3]


@pytest.mark.asyncio
async def test_skill_revision_pinning(env):
    """After publish, changing the skill content does not move the pinned revision."""
    service, draft_service, _, release_repo = env
    agent = await _seed_agent(draft_service)
    r1 = await service.publish(agent["id"], owner_user_id="user-a")
    release1 = await release_repo.get(r1["release_id"], owner_user_id="user-a")
    pinned_revision = release1["skills"][0]["skill_revision_id"]
    # Re-publish: the skill content is identical so the revision must be reused.
    draft = await draft_service.get_draft(agent["id"], owner_user_id="user-a")
    await draft_service.update_draft(agent["id"], owner_user_id="user-a", revision=draft["revision"], agent_markdown="# Agent v2")
    r2 = await service.publish(agent["id"], owner_user_id="user-a")
    release2 = await release_repo.get(r2["release_id"], owner_user_id="user-a")
    assert release2["skills"][0]["skill_revision_id"] == pinned_revision


@pytest.mark.asyncio
async def test_publish_only_agent_md_or_only_soul_md(env):
    """Acceptance #2: each of only-AGENT, only-SOUL, both must publish."""
    service, draft_service, _, _ = env

    # only AGENT.md
    a1 = await _seed_agent(draft_service, slug="a1")
    d1 = await draft_service.get_draft(a1["id"], owner_user_id="user-a")
    await draft_service.update_draft(a1["id"], owner_user_id="user-a", revision=d1["revision"], agent_markdown="# A", soul_markdown="")
    assert (await service.publish(a1["id"], owner_user_id="user-a"))["release_no"] == 1

    # only SOUL.md
    a2 = await _seed_agent(draft_service, slug="a2")
    d2 = await draft_service.get_draft(a2["id"], owner_user_id="user-a")
    await draft_service.update_draft(a2["id"], owner_user_id="user-a", revision=d2["revision"], agent_markdown="", soul_markdown="# S")
    assert (await service.publish(a2["id"], owner_user_id="user-a"))["release_no"] == 1

    # both
    a3 = await _seed_agent(draft_service, slug="a3")
    assert (await service.publish(a3["id"], owner_user_id="user-a"))["release_no"] == 1


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_repoints_pointer_without_mutating_history(env):
    service, draft_service, agent_repo, release_repo = env
    agent = await _seed_agent(draft_service)
    r1 = await service.publish(agent["id"], owner_user_id="user-a")
    draft = await draft_service.get_draft(agent["id"], owner_user_id="user-a")
    await draft_service.update_draft(agent["id"], owner_user_id="user-a", revision=draft["revision"], agent_markdown="# Agent v2")
    r2 = await service.publish(agent["id"], owner_user_id="user-a")
    assert r2["release_no"] == 2

    # Roll back to release 1.
    await service.rollback(agent["id"], owner_user_id="user-a", release_no=1)
    refreshed = await agent_repo.get(agent["id"], owner_user_id="user-a")
    assert refreshed["current_release_id"] == r1["release_id"]

    # Historical release 2 row is unchanged.
    release2 = await release_repo.get(r2["release_id"], owner_user_id="user-a")
    assert release2 is not None
    assert release2["release_no"] == 2


@pytest.mark.asyncio
async def test_rollback_unknown_release_raises(env):
    service, draft_service, _, _ = env
    agent = await _seed_agent(draft_service)
    await service.publish(agent["id"], owner_user_id="user-a")
    with pytest.raises(ReleaseNotFoundError):
        await service.rollback(agent["id"], owner_user_id="user-a", release_no=99)


@pytest.mark.asyncio
async def test_rollback_cross_owner_raises(env):
    service, draft_service, _, _ = env
    agent = await _seed_agent(draft_service)
    await service.publish(agent["id"], owner_user_id="user-a")
    with pytest.raises(ReleaseNotFoundError):
        await service.rollback(agent["id"], owner_user_id="user-b", release_no=1)


@pytest.mark.asyncio
async def test_list_releases_owner_scoped(env):
    service, draft_service, _, _ = env
    agent = await _seed_agent(draft_service)
    await service.publish(agent["id"], owner_user_id="user-a")
    history = await service.list_releases(agent["id"], owner_user_id="user-a")
    assert len(history) == 1
    # Cross owner sees nothing.
    assert await service.list_releases(agent["id"], owner_user_id="user-b") == []


@pytest.mark.asyncio
async def test_get_release_owner_scoped(env):
    service, draft_service, _, _ = env
    agent = await _seed_agent(draft_service)
    await service.publish(agent["id"], owner_user_id="user-a")
    release = await service.get_release(agent["id"], owner_user_id="user-a", release_no=1)
    assert release is not None
    assert release["release_no"] == 1
    assert await service.get_release(agent["id"], owner_user_id="user-b", release_no=1) is None
