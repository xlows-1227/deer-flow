"""Tests for the legacy-agent import service (F1.7).

Existing user-level custom agents live on disk as
``users/{user_id}/agents/{name}/{SOUL.md,config.yaml}``. The import service
lists them as candidates and turns each into a ``status=draft`` published-agent
+ draft pair, mapping SOUL.md -> soul_markdown, config model/tool_groups/skills
-> draft fields, and reporting any skill name that cannot be resolved. Imports
are never auto-published and never delete the source files. Re-importing the
same slug is rejected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from deerflow.publishing.import_service import (
    AgentImportService,
    ImportAlreadyExistsError,
)


class _MemAgents:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def create_agent(self, *, owner_user_id, slug, display_name, description=None, avatar_ref=None, agent_id=None):
        if any(r["owner_user_id"] == owner_user_id and r["slug"] == slug for r in self.rows.values()):
            raise ValueError(f"Agent slug already exists for owner: {slug}")
        agent_id = agent_id or f"pa_{slug}"
        self.rows[agent_id] = {
            "id": agent_id,
            "owner_user_id": owner_user_id,
            "slug": slug,
            "display_name": display_name,
            "description": description,
            "avatar_ref": avatar_ref,
            "status": "draft",
            "current_release_id": None,
        }
        return dict(self.rows[agent_id])


class _MemDrafts:
    def __init__(self, agents: _MemAgents) -> None:
        self.drafts: dict[str, dict[str, Any]] = {}
        self._agents = agents

    async def get(self, agent_id, *, owner_user_id):
        return dict(self.drafts[agent_id]) if agent_id in self.drafts else None

    async def update_with_revision(self, agent_id, *, owner_user_id, revision, **fields):
        d = self.drafts.get(agent_id)
        if d is None or d["revision"] != revision:
            return None
        for k, v in fields.items():
            if v is not None:
                d[k] = v
        d["revision"] = revision + 1
        return dict(d)

    async def replace_skills(self, agent_id, *, owner_user_id, skills):
        self.drafts[agent_id]["skills"] = list(skills)
        return dict(self.drafts[agent_id])


class _MemSkillsIndex:
    def __init__(self, known: set[str]) -> None:
        self.known = known

    def is_selectable_by(self, name, owner_user_id):  # noqa: ARG002
        return name in self.known


def _write_legacy_agent(base: Path, user_id: str, name: str, *, soul="# I am " + "x", model="gpt-x", tool_groups=None, skills=None):
    agent_dir = base / "users" / user_id / "agents" / name
    agent_dir.mkdir(parents=True)
    (agent_dir / "SOUL.md").write_text(soul, encoding="utf-8")
    import yaml

    cfg = {"name": name}
    if model:
        cfg["model"] = model
    if tool_groups is not None:
        cfg["tool_groups"] = tool_groups
    if skills is not None:
        cfg["skills"] = skills
    (agent_dir / "config.yaml").write_text(yaml.dump(cfg), encoding="utf-8")
    return agent_dir


def _service(base: Path, known_skills: set[str] | None = None):
    agents = _MemAgents()
    drafts = _MemDrafts(agents)

    # Patch create_agent to seed the draft row (mirrors the real repo pair).
    real_create = agents.create_agent

    async def create_and_seed(*, owner_user_id, slug, display_name, description=None, avatar_ref=None, agent_id=None):
        agent = await real_create(owner_user_id=owner_user_id, slug=slug, display_name=display_name, description=description, avatar_ref=avatar_ref, agent_id=agent_id)
        drafts.drafts[agent["id"]] = {
            "agent_id": agent["id"],
            "agent_markdown": "",
            "soul_markdown": "",
            "model_name": None,
            "tool_groups": [],
            "quota_overrides": {},
            "revision": 1,
            "skills": [],
            "connector_grants": [],
        }
        return agent

    agents.create_agent = create_and_seed  # type: ignore[assignment]
    return AgentImportService(
        published_agent_repo=agents,
        draft_repo=drafts,
        skills_index=_MemSkillsIndex(known_skills or {"reporting"}),
        base_dir=base,
    )


def test_list_candidates_finds_legacy_agents(tmp_path):
    _write_legacy_agent(tmp_path, "user-a", "bot-1")
    _write_legacy_agent(tmp_path, "user-a", "bot-2")
    service = _service(tmp_path)
    candidates = service.list_candidates("user-a")
    names = {c.name for c in candidates}
    assert names == {"bot-1", "bot-2"}


def test_candidate_carries_soul_and_config(tmp_path):
    _write_legacy_agent(tmp_path, "user-a", "bot", soul="# Hello", model="gpt-x", tool_groups=["web"], skills=["reporting"])
    service = _service(tmp_path)
    candidate = service.list_candidates("user-a")[0]
    assert candidate.soul_markdown == "# Hello"
    assert candidate.model_name == "gpt-x"
    assert candidate.tool_groups == ["web"]
    assert candidate.skills == ["reporting"]


@pytest.mark.anyio
async def test_import_creates_draft_with_mapped_fields(tmp_path):
    _write_legacy_agent(tmp_path, "user-a", "bot", soul="# Soul", model="gpt-x", tool_groups=["web"], skills=["reporting"])
    service = _service(tmp_path, known_skills={"reporting"})
    report = await service.import_agent("user-a", "bot")
    assert report["status"] == "draft"
    assert report["current_release_id"] is None
    assert report["unresolved_skills"] == []
    agent_id = report["agent_id"]
    # The draft holds the mapped content.
    from deerflow.persistence.published_agent import AgentDraftRepository  # noqa: F401

    # Verify through the in-memory drafts directly.
    drafts = service._drafts.drafts  # noqa: SLF001
    draft = drafts[agent_id]
    assert draft["soul_markdown"] == "# Soul"
    assert draft["model_name"] == "gpt-x"
    assert draft["tool_groups"] == ["web"]
    assert [s["skill_name"] for s in draft["skills"]] == ["reporting"]


@pytest.mark.anyio
async def test_import_reports_unresolved_skills_without_blocking(tmp_path):
    _write_legacy_agent(tmp_path, "user-a", "bot", skills=["reporting", "ghost"])
    service = _service(tmp_path, known_skills={"reporting"})
    report = await service.import_agent("user-a", "bot")
    assert report["unresolved_skills"] == ["ghost"]
    # Agent still imported; unresolved skill simply not selected.
    drafts = service._drafts.drafts  # noqa: SLF001
    draft = drafts[report["agent_id"]]
    assert [s["skill_name"] for s in draft["skills"]] == ["reporting"]


@pytest.mark.anyio
async def test_import_agent_markdown_empty_when_no_agent_md(tmp_path):
    """Legacy agents have no AGENT.md; the imported draft's agent_markdown is empty."""
    _write_legacy_agent(tmp_path, "user-a", "bot")
    service = _service(tmp_path)
    report = await service.import_agent("user-a", "bot")
    drafts = service._drafts.drafts  # noqa: SLF001
    assert drafts[report["agent_id"]]["agent_markdown"] == ""


@pytest.mark.anyio
async def test_duplicate_import_rejected(tmp_path):
    _write_legacy_agent(tmp_path, "user-a", "bot")
    service = _service(tmp_path)
    await service.import_agent("user-a", "bot")
    with pytest.raises(ImportAlreadyExistsError):
        await service.import_agent("user-a", "bot")


@pytest.mark.anyio
async def test_import_does_not_delete_source_files(tmp_path):
    agent_dir = _write_legacy_agent(tmp_path, "user-a", "bot")
    service = _service(tmp_path)
    await service.import_agent("user-a", "bot")
    assert agent_dir.exists()
    assert (agent_dir / "SOUL.md").exists()
    assert (agent_dir / "config.yaml").exists()


@pytest.mark.anyio
async def test_import_status_is_always_draft(tmp_path):
    _write_legacy_agent(tmp_path, "user-a", "bot")
    service = _service(tmp_path)
    report = await service.import_agent("user-a", "bot")
    assert report["status"] == "draft"
    assert report["current_release_id"] is None
