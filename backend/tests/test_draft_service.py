"""Unit tests for the DraftService (F1.4).

The service is exercised against in-memory fakes mirroring the repository
contracts, so these tests run without a database and focus purely on the
authorization / validation rules: skill ownership, connector ownership,
revision-conflict semantics, and the guarantee that saving a draft never
mutates ``current_release_id``.
"""

from __future__ import annotations

from typing import Any

import pytest

from deerflow.publishing.draft_service import (
    ConnectorNotGrantableError,
    DraftConflictError,
    DraftService,
    InvalidAgentStateTransitionError,
    SkillNotSelectableError,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakePublishedAgentRepo:
    def __init__(self, draft_repo: FakeDraftRepo | None = None) -> None:
        self.agents: dict[str, dict[str, Any]] = {}
        self._draft_repo = draft_repo

    async def create_agent(self, *, owner_user_id, slug, display_name, description=None, avatar_ref=None, agent_id=None):
        agent_id = agent_id or f"pa_{slug}"
        if any(a["owner_user_id"] == owner_user_id and a["slug"] == slug for a in self.agents.values()):
            raise ValueError(f"Agent slug already exists for owner: {slug}")
        agent = {
            "id": agent_id,
            "owner_user_id": owner_user_id,
            "slug": slug,
            "display_name": display_name,
            "description": description,
            "avatar_ref": avatar_ref,
            "status": "draft",
            "current_release_id": None,
            "created_at": None,
            "updated_at": None,
        }
        self.agents[agent_id] = agent
        if self._draft_repo is not None:
            self._draft_repo._seed(agent_id)
        return dict(agent)

    async def get(self, agent_id, *, owner_user_id):
        a = self.agents.get(agent_id)
        if a is None or a["owner_user_id"] != owner_user_id:
            return None
        return dict(a)

    async def list_by_owner(self, owner_user_id):
        return [dict(a) for a in self.agents.values() if a["owner_user_id"] == owner_user_id]

    async def update_meta(self, agent_id, *, owner_user_id, **fields):
        a = self.agents.get(agent_id)
        if a is None or a["owner_user_id"] != owner_user_id:
            return None
        for k, v in fields.items():
            if v is not None:
                a[k] = v
        return dict(a)

    async def set_status(self, agent_id, *, owner_user_id, status):
        a = self.agents.get(agent_id)
        if a is None or a["owner_user_id"] != owner_user_id:
            return False
        a["status"] = status
        return True

    async def transition_status(
        self,
        agent_id,
        *,
        owner_user_id,
        from_statuses,
        to_status,
        require_current_release=False,
    ):
        a = self.agents.get(agent_id)
        if a is None or a["owner_user_id"] != owner_user_id or a["status"] not in from_statuses or (require_current_release and a["current_release_id"] is None):
            return False
        a["status"] = to_status
        return True

    async def set_current_release(self, agent_id, *, owner_user_id, release_id):
        a = self.agents.get(agent_id)
        if a is None or a["owner_user_id"] != owner_user_id:
            return False
        a["current_release_id"] = release_id
        if release_id is not None and a["status"] == "draft":
            a["status"] = "published"
        return True


class FakeDraftRepo:
    def __init__(self, agent_repo: FakePublishedAgentRepo | None = None) -> None:
        self.drafts: dict[str, dict[str, Any]] = {}
        self._agent_repo = agent_repo

    def _seed(self, agent_id):
        self.drafts.setdefault(
            agent_id,
            {
                "agent_id": agent_id,
                "agent_markdown": "",
                "soul_markdown": "",
                "model_name": None,
                "tool_groups": [],
                "quota_overrides": {},
                "revision": 1,
                "updated_by": "",
                "skills": [],
                "connector_grants": [],
            },
        )

    def _owned(self, agent_id, owner_user_id) -> bool:
        if self._agent_repo is None:
            return True
        a = self._agent_repo.agents.get(agent_id)
        return a is not None and a["owner_user_id"] == owner_user_id

    async def get(self, agent_id, *, owner_user_id):
        if agent_id not in self.drafts or not self._owned(agent_id, owner_user_id):
            return None
        return dict(self.drafts.get(agent_id))

    async def update_with_revision(self, agent_id, *, owner_user_id, revision, **fields):
        d = self.drafts.get(agent_id)
        if d is None or not self._owned(agent_id, owner_user_id) or d["revision"] != revision:
            return None
        model_name_provided = fields.pop("model_name_provided", False)
        for k, v in fields.items():
            if v is not None or (k == "model_name" and model_name_provided):
                d[k] = v
        d["revision"] = revision + 1
        return dict(d)

    async def update_bundle(self, agent_id, *, owner_user_id, revision, skills=None, connector_grants=None, **fields):
        d = self.drafts.get(agent_id)
        if d is None or not self._owned(agent_id, owner_user_id) or d["revision"] != revision:
            return None
        model_name_provided = fields.pop("model_name_provided", False)
        for k, v in fields.items():
            if v is not None or (k == "model_name" and model_name_provided):
                d[k] = v
        if skills is not None:
            d["skills"] = list(skills)
        if connector_grants is not None:
            d["connector_grants"] = list(connector_grants)
        d["revision"] = revision + 1
        return dict(d)

    async def replace_skills(self, agent_id, *, owner_user_id, skills):
        d = self.drafts.get(agent_id)
        if d is None or not self._owned(agent_id, owner_user_id):
            return None
        d["skills"] = list(skills)
        return dict(d)

    async def replace_connector_grants(self, agent_id, *, owner_user_id, grants):
        d = self.drafts.get(agent_id)
        if d is None or not self._owned(agent_id, owner_user_id):
            return None
        d["connector_grants"] = list(grants)
        return dict(d)


class FakeSkillsIndex:
    """name -> {"visibility": public|private, "owner": user_id|None}"""

    def __init__(self, skills: dict[str, dict[str, Any]] | None = None) -> None:
        self.skills = skills or {}

    def is_selectable_by(self, name: str, owner_user_id: str) -> bool:
        info = self.skills.get(name)
        if info is None:
            return False
        if info["visibility"] == "public":
            return True
        return info["owner"] == owner_user_id

    def get(self, name: str) -> dict[str, Any] | None:
        return self.skills.get(name)


class FakeConnectorRepo:
    def __init__(self, owners: dict[str, str] | None = None, capabilities: dict[str, set[str]] | None = None) -> None:
        # connector_instance_id -> owner_user_id
        self.owners = owners or {}
        self.capabilities = capabilities or {connector_id: {"database.query"} for connector_id in self.owners}

    async def get_instance(self, connector_id, *, owner_id=...):
        owner = self.owners.get(connector_id)
        if owner is None:
            return None
        if owner_id is not ... and owner != owner_id:
            return None
        return {
            "id": connector_id,
            "owner_id": owner,
            "status": "active",
            "supported_capabilities": tuple(sorted(self.capabilities.get(connector_id, set()))),
        }


@pytest.fixture()
def service():
    agent_repo = FakePublishedAgentRepo()
    draft_repo = FakeDraftRepo(agent_repo=agent_repo)
    agent_repo._draft_repo = draft_repo
    return DraftService(
        published_agent_repo=agent_repo,
        draft_repo=draft_repo,
        skills_index=FakeSkillsIndex(
            {
                "reporting": {"visibility": "public", "owner": None},
                "secret-tool": {"visibility": "private", "owner": "user-a"},
            }
        ),
        connector_repo=FakeConnectorRepo({"conn_1": "user-a", "conn_2": "user-a"}),
    )


# ---------------------------------------------------------------------------
# create / get / list
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_agent_and_empty_draft(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    assert agent["status"] == "draft"
    draft = await service.get_draft(agent["id"], owner_user_id="user-a")
    assert draft["revision"] == 1
    assert draft["agent_markdown"] == ""


@pytest.mark.anyio
async def test_get_draft_cross_owner_returns_none(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    assert await service.get_draft(agent["id"], owner_user_id="user-b") is None


# ---------------------------------------------------------------------------
# update draft (revision conflict)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_draft_bumps_revision(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    updated = await service.update_draft(agent["id"], owner_user_id="user-a", revision=1, soul_markdown="# Soul")
    assert updated["revision"] == 2
    assert updated["soul_markdown"] == "# Soul"


@pytest.mark.anyio
async def test_update_draft_revision_conflict_raises(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    await service.update_draft(agent["id"], owner_user_id="user-a", revision=1, soul_markdown="# v1")
    with pytest.raises(DraftConflictError):
        await service.update_draft(agent["id"], owner_user_id="user-a", revision=1, soul_markdown="# stale")


@pytest.mark.anyio
async def test_update_draft_cross_owner_raises_conflict(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    with pytest.raises(DraftConflictError):
        await service.update_draft(agent["id"], owner_user_id="user-b", revision=1, soul_markdown="evil")


@pytest.mark.anyio
async def test_update_draft_does_not_touch_current_release(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    await service.update_draft(agent["id"], owner_user_id="user-a", revision=1, soul_markdown="# Soul")
    again = await service.get_agent(agent["id"], owner_user_id="user-a")
    assert again["current_release_id"] is None


# ---------------------------------------------------------------------------
# skill selection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_replace_skills_accepts_public_and_own_private(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    await service.set_skills(
        agent["id"],
        owner_user_id="user-a",
        skills=[{"skill_name": "reporting", "source": "public"}, {"skill_name": "secret-tool", "source": "private"}],
    )
    draft = await service.get_draft(agent["id"], owner_user_id="user-a")
    assert {s["skill_name"] for s in draft["skills"]} == {"reporting", "secret-tool"}


@pytest.mark.anyio
async def test_replace_skills_rejects_other_owners_private(service):
    agent = await service.create_agent(owner_user_id="user-b", slug="bot", display_name="Bot")
    with pytest.raises(SkillNotSelectableError):
        await service.set_skills(
            agent["id"],
            owner_user_id="user-b",
            skills=[{"skill_name": "secret-tool", "source": "private"}],  # owned by user-a
        )


@pytest.mark.anyio
async def test_replace_skills_rejects_unknown_skill(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    with pytest.raises(SkillNotSelectableError):
        await service.set_skills(agent["id"], owner_user_id="user-a", skills=[{"skill_name": "ghost", "source": "public"}])


# ---------------------------------------------------------------------------
# connector grants
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_replace_connector_grants_accepts_own_connector(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    await service.set_connector_grants(
        agent["id"],
        owner_user_id="user-a",
        grants=[{"connector_instance_id": "conn_1", "capability": "database.query"}],
    )
    draft = await service.get_draft(agent["id"], owner_user_id="user-a")
    assert draft["connector_grants"] == [{"connector_instance_id": "conn_1", "capability": "database.query"}]


@pytest.mark.anyio
async def test_replace_connector_grants_rejects_capability_not_supported_by_type(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    with pytest.raises(ConnectorNotGrantableError, match="capability not supported"):
        await service.set_connector_grants(
            agent["id"],
            owner_user_id="user-a",
            grants=[{"connector_instance_id": "conn_1", "capability": "mail.send"}],
        )
    draft = await service.get_draft(agent["id"], owner_user_id="user-a")
    assert draft["connector_grants"] == []


@pytest.mark.anyio
async def test_update_bundle_rejects_duplicate_skills_before_repository_write(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    with pytest.raises(SkillNotSelectableError, match="duplicate skill"):
        await service.update_draft_bundle(
            agent["id"],
            owner_user_id="user-a",
            revision=1,
            skills=[{"skill_name": "reporting"}, {"skill_name": "reporting"}],
        )
    draft = await service.get_draft(agent["id"], owner_user_id="user-a")
    assert draft["revision"] == 1
    assert draft["skills"] == []


@pytest.mark.anyio
async def test_update_bundle_rejects_duplicate_connector_grants_before_repository_write(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    grant = {"connector_instance_id": "conn_1", "capability": "database.query"}
    with pytest.raises(ConnectorNotGrantableError, match="duplicate connector grant"):
        await service.update_draft_bundle(
            agent["id"],
            owner_user_id="user-a",
            revision=1,
            connector_grants=[grant, grant],
        )
    draft = await service.get_draft(agent["id"], owner_user_id="user-a")
    assert draft["revision"] == 1
    assert draft["connector_grants"] == []


@pytest.mark.anyio
async def test_replace_connector_grants_rejects_other_owners_connector(service):
    agent = await service.create_agent(owner_user_id="user-b", slug="bot", display_name="Bot")
    with pytest.raises(ConnectorNotGrantableError):
        await service.set_connector_grants(
            agent["id"],
            owner_user_id="user-b",
            grants=[{"connector_instance_id": "conn_1", "capability": "database.query"}],  # owned by user-a
        )


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_suspend_resume_archive(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    with pytest.raises(InvalidAgentStateTransitionError):
        await service.suspend(agent["id"], owner_user_id="user-a")
    with pytest.raises(InvalidAgentStateTransitionError):
        await service.resume(agent["id"], owner_user_id="user-a")

    await service._agents.set_current_release(
        agent["id"],
        owner_user_id="user-a",
        release_id="rel-1",
    )
    await service.suspend(agent["id"], owner_user_id="user-a")
    assert (await service.get_agent(agent["id"], owner_user_id="user-a"))["status"] == "suspended"
    await service.resume(agent["id"], owner_user_id="user-a")
    assert (await service.get_agent(agent["id"], owner_user_id="user-a"))["status"] == "published"
    await service.archive(agent["id"], owner_user_id="user-a")
    assert (await service.get_agent(agent["id"], owner_user_id="user-a"))["status"] == "archived"


@pytest.mark.anyio
async def test_lifecycle_cross_owner_noop(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bot", display_name="Bot")
    assert await service.suspend(agent["id"], owner_user_id="user-b") is False
    assert (await service.get_agent(agent["id"], owner_user_id="user-a"))["status"] == "draft"


# ---------------------------------------------------------------------------
# update_draft_bundle (atomic main + sub-tables under revision check)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_draft_bundle_applies_all_fields(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="bundle", display_name="B")
    result = await service.update_draft_bundle(
        agent["id"],
        owner_user_id="user-a",
        revision=1,
        soul_markdown="# Soul",
        tool_groups=["web"],
        skills=[{"skill_name": "reporting", "source": "public"}],
        connector_grants=[{"connector_instance_id": "conn_1", "capability": "database.query"}],
    )
    assert result["revision"] == 2
    assert result["soul_markdown"] == "# Soul"
    assert result["tool_groups"] == ["web"]
    assert [s["skill_name"] for s in result["skills"]] == ["reporting"]
    assert result["connector_grants"] == [{"connector_instance_id": "conn_1", "capability": "database.query"}]


@pytest.mark.anyio
async def test_update_draft_bundle_stale_revision_leaves_subtables_unchanged(service):
    """Critical-3: a stale revision must not mutate skills/connector_grants."""
    agent = await service.create_agent(owner_user_id="user-a", slug="atomic", display_name="A")
    # First successful bundle write: skills = [reporting], revision -> 2.
    await service.update_draft_bundle(
        agent["id"],
        owner_user_id="user-a",
        revision=1,
        skills=[{"skill_name": "reporting", "source": "public"}],
    )
    before = await service.get_draft(agent["id"], owner_user_id="user-a")
    skills_before = {s["skill_name"] for s in before["skills"]}

    # Stale-revision bundle that would change skills + grants. Must raise 409.
    # Use a selectable skill (secret-tool, owned by user-a) that differs from
    # the current selection so we can assert the sub-table did NOT change.
    with pytest.raises(DraftConflictError):
        await service.update_draft_bundle(
            agent["id"],
            owner_user_id="user-a",
            revision=1,  # stale
            skills=[{"skill_name": "secret-tool", "source": "private"}],
            soul_markdown="# changed-by-stale",
            connector_grants=[{"connector_instance_id": "conn_1", "capability": "database.query"}],
        )
    after = await service.get_draft(agent["id"], owner_user_id="user-a")
    # Sub-tables and revision untouched.
    assert {s["skill_name"] for s in after["skills"]} == skills_before
    assert after["connector_grants"] == []
    assert after["revision"] == before["revision"]
    assert after["soul_markdown"] == ""


@pytest.mark.anyio
async def test_update_draft_bundle_rejects_unselectable_skill_before_write(service):
    agent = await service.create_agent(owner_user_id="user-a", slug="validate", display_name="V")
    with pytest.raises(SkillNotSelectableError):
        await service.update_draft_bundle(
            agent["id"],
            owner_user_id="user-a",
            revision=1,
            skills=[{"skill_name": "ghost", "source": "public"}],
            soul_markdown="# should-not-apply",
        )
    # Nothing written.
    after = await service.get_draft(agent["id"], owner_user_id="user-a")
    assert after["revision"] == 1
    assert after["soul_markdown"] == ""


# ---------------------------------------------------------------------------
# filter_selectable_skills (unresolved feedback)
# ---------------------------------------------------------------------------


def test_filter_selectable_skills_returns_both_selectable_and_unresolved(service):
    """Sixth-review Important-1: filter_selectable_skills must return unresolved
    names alongside the selectable subset so tools can report them."""
    selectable, unresolved = service.filter_selectable_skills(["reporting", "ghost", "secret-tool"], owner_user_id="user-a")
    assert "reporting" in selectable
    assert "secret-tool" in selectable  # private skill owned by user-a
    assert "ghost" in unresolved


def test_filter_selectable_skills_empty_input():
    """Empty input returns empty selectable and unresolved."""
    svc = DraftService(
        published_agent_repo=FakePublishedAgentRepo(draft_repo := FakeDraftRepo()),
        draft_repo=draft_repo,
        skills_index=FakeSkillsIndex({}),
        connector_repo=FakeConnectorRepo(),
    )
    selectable, unresolved = svc.filter_selectable_skills([], owner_user_id="user-a")
    assert selectable == []
    assert unresolved == []
