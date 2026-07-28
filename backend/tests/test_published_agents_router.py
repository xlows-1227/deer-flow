"""FastAPI TestClient tests for the published-agents draft CRUD router (F1.4).

The router is mounted under ``/api/published-agents`` and requires a browser
session. Tests use a ``UserMiddleware`` that stamps ``request.state.user`` (the
same shape ``AuthMiddleware`` produces in production) and override the
``DraftService`` dependency with one backed by in-memory fakes, so no database
is needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.gateway.routers import published_agents
from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import (
    AgentDraftRepository,
    PublishedAgentRepository,
)
from deerflow.publishing.draft_service import DraftService


class _SessionMiddleware(BaseHTTPMiddleware):
    """Stamps a user onto request.state, mirroring production AuthMiddleware."""

    def __init__(self, app, user_id: str, auth_method: str = "session") -> None:  # noqa: D401
        super().__init__(app)
        self.user_id = user_id
        self.auth_method = auth_method

    async def dispatch(self, request: Request, call_next):
        request.state.user = SimpleNamespace(id=self.user_id)
        request.state.auth_method = self.auth_method
        return await call_next(request)


# ---------------------------------------------------------------------------
# In-memory collaborators (mirrors of the repo contracts)
# ---------------------------------------------------------------------------


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
            "created_at": "2026-07-12T00:00:00Z",
            "updated_at": "2026-07-12T00:00:00Z",
        }
        return dict(self.rows[agent_id])

    async def get(self, agent_id, *, owner_user_id):
        r = self.rows.get(agent_id)
        return dict(r) if r and r["owner_user_id"] == owner_user_id else None

    async def list_by_owner(self, owner_user_id):
        return [dict(r) for r in self.rows.values() if r["owner_user_id"] == owner_user_id]

    async def set_status(self, agent_id, *, owner_user_id, status):
        r = self.rows.get(agent_id)
        if not r or r["owner_user_id"] != owner_user_id:
            return False
        r["status"] = status
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
        r = self.rows.get(agent_id)
        if not r or r["owner_user_id"] != owner_user_id or r["status"] not in from_statuses or (require_current_release and r["current_release_id"] is None):
            return False
        r["status"] = to_status
        return True

    async def set_current_release(self, agent_id, *, owner_user_id, release_id):
        r = self.rows.get(agent_id)
        if not r or r["owner_user_id"] != owner_user_id:
            return False
        r["current_release_id"] = release_id
        if release_id is not None and r["status"] == "draft":
            r["status"] = "published"
        return True

    async def update_meta(self, agent_id, *, owner_user_id, **fields):
        r = self.rows.get(agent_id)
        if not r or r["owner_user_id"] != owner_user_id:
            return None
        for k, v in fields.items():
            if v is not None:
                r[k] = v
        return dict(r)


class _MemDrafts:
    def __init__(self, agents: _MemAgents) -> None:
        self.drafts: dict[str, dict[str, Any]] = {}
        self._agents = agents

    def _seed(self, agent_id):
        self.drafts.setdefault(
            agent_id,
            {"agent_id": agent_id, "agent_markdown": "", "soul_markdown": "", "model_name": None, "tool_groups": [], "quota_overrides": {}, "revision": 1, "skills": [], "connector_grants": []},
        )

    def _owned(self, agent_id, owner_user_id):
        r = self._agents.rows.get(agent_id)
        return r is not None and r["owner_user_id"] == owner_user_id

    async def get(self, agent_id, *, owner_user_id):
        if not self._owned(agent_id, owner_user_id):
            return None
        return dict(self.drafts.get(agent_id)) if agent_id in self.drafts else None

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
        """Atomic counterpart: revision check gates the whole update (Critical-3)."""
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


class _MemSkillsIndex:
    def is_selectable_by(self, name, owner_user_id):
        return name in {"reporting", "public-tool"}

    def get(self, name):
        if name == "reporting":
            return {
                "visibility": "private",
                "owner": "owner",
                "description": "Owner reporting workflow",
                "caps": ["database.query"],
            }
        return (
            {
                "visibility": "public",
                "description": "Public helper",
                "caps": [],
            }
            if name == "public-tool"
            else None
        )

    def list_selectable_by(self, owner_user_id):
        return [
            {
                "skill_name": "public-tool",
                "source": "public",
                "display_name": "公共工具",
                "description": "Public helper",
                "description_zh": "用于公共辅助任务。",
                "declared_connector_caps": [],
            },
            {
                "skill_name": "reporting",
                "source": "private",
                "display_name": "经营报表",
                "description": "Owner reporting workflow",
                "description_zh": "查询业务数据并生成经营报表。",
                "declared_connector_caps": ["database.query"],
            },
        ]


class _MemConnectorRepo:
    async def get_instance(self, connector_id, *, owner_id=...):
        if connector_id == "conn_own":
            return {
                "id": connector_id,
                "owner_id": owner_id,
                "status": "active",
                "supported_capabilities": ("database.query",),
            }
        return None


def _build_service(owner: str) -> published_agents.DraftService:
    from deerflow.publishing.draft_service import DraftService

    agents = _MemAgents()

    # Patch create_agent to also seed the draft (mirror the real repo pair).
    real_create = agents.create_agent

    async def create_agent(*, owner_user_id, slug, display_name, description=None, avatar_ref=None, agent_id=None):
        agent = await real_create(owner_user_id=owner_user_id, slug=slug, display_name=display_name, description=description, avatar_ref=avatar_ref, agent_id=agent_id)
        drafts._seed(agent["id"])
        return agent

    agents.create_agent = create_agent  # type: ignore[assignment]
    drafts = _MemDrafts(agents)
    return DraftService(
        published_agent_repo=agents,
        draft_repo=drafts,
        skills_index=_MemSkillsIndex(),
        connector_repo=_MemConnectorRepo(),
    )


class _StubPublishService:
    """Minimal stub satisfying the router's publish-service surface.

    Only ``list_releases`` is exercised at the router level here; full publish
    behaviour is covered by the DB-backed ``test_publish_service.py``.
    """

    def __init__(self) -> None:
        self.releases: dict[str, list[dict[str, Any]]] = {}

    async def publish(self, agent_id, *, owner_user_id):  # noqa: ARG002
        return {
            "release_id": "rel-1",
            "release_no": 1,
            "published_at": "2026-07-14T00:00:00Z",
        }

    async def list_releases(self, agent_id, *, owner_user_id):
        return list(self.releases.get(agent_id, []))


def _make_client(owner: str = None):
    owner = owner or str(uuid4())
    app = FastAPI()
    app.add_middleware(_SessionMiddleware, user_id=owner)
    app.include_router(published_agents.router)
    service = _build_service(owner)
    publish_service = _StubPublishService()
    app.dependency_overrides[published_agents.get_draft_service] = lambda: service
    app.dependency_overrides[published_agents.get_publish_service] = lambda: publish_service
    client = TestClient(app)
    client.app.state.test_publish_service = publish_service
    return client, service, owner


# ---------------------------------------------------------------------------
# endpoint coverage
# ---------------------------------------------------------------------------


def test_create_and_get_agent():
    client, _, _ = _make_client()
    created = client.post("/api/published-agents", json={"slug": "bot", "display_name": "Bot"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["slug"] == "bot"
    assert body["status"] == "draft"
    detail = client.get(f"/api/published-agents/{body['id']}")
    assert detail.status_code == 200
    assert "agent_markdown" in detail.json()["draft"]


def test_list_draft_options_returns_owner_selectable_skill_metadata():
    client, _service, _owner = _make_client()
    agent = client.post(
        "/api/published-agents",
        json={"slug": "studio", "display_name": "Studio"},
    ).json()

    response = client.get(f"/api/published-agents/{agent['id']}/draft/options")

    assert response.status_code == 200
    assert response.json() == {
        "skills": [
            {
                "skill_name": "public-tool",
                "source": "public",
                "display_name": "公共工具",
                "description": "Public helper",
                "description_zh": "用于公共辅助任务。",
                "declared_connector_caps": [],
            },
            {
                "skill_name": "reporting",
                "source": "private",
                "display_name": "经营报表",
                "description": "Owner reporting workflow",
                "description_zh": "查询业务数据并生成经营报表。",
                "declared_connector_caps": ["database.query"],
            },
        ]
    }


def test_quota_policy_distinguishes_platform_defaults_and_draft_overrides():
    client, _, _ = _make_client()
    agent = client.post(
        "/api/published-agents",
        json={"slug": "quota-agent", "display_name": "Quota Agent"},
    ).json()
    patched = client.patch(
        f"/api/published-agents/{agent['id']}/draft",
        json={"revision": 1, "quota_overrides": {"daily_runs": 25}},
    )
    assert patched.status_code == 200

    response = client.get(f"/api/published-agents/{agent['id']}/quota")

    assert response.status_code == 200
    assert response.json()["platform_defaults"]["daily_runs"] == 1000
    assert response.json()["owner_overrides"] == {"daily_runs": 25}
    assert response.json()["effective"]["daily_runs"] == 25


def test_create_agent_rejects_slug_that_runtime_cannot_resolve():
    client, _, _ = _make_client()
    for slug in ("bad/name", "has space", "under_score"):
        response = client.post(
            "/api/published-agents",
            json={"slug": slug, "display_name": "Invalid"},
        )
        assert response.status_code == 422


def test_create_agent_preserves_canonical_slug_case():
    client, _, _ = _make_client()
    response = client.post(
        "/api/published-agents",
        json={"slug": "MiXeD", "display_name": "Mixed"},
    )
    assert response.status_code == 201
    assert response.json()["slug"] == "MiXeD"


def test_publish_draft_revision_conflict_returns_409():
    from unittest.mock import AsyncMock

    from deerflow.publishing.publish_service import PublishError
    from deerflow.publishing.validation import PublishViolation

    client, _, _ = _make_client()
    agent = client.post(
        "/api/published-agents",
        json={"slug": "bot", "display_name": "Bot"},
    ).json()
    client.app.state.test_publish_service.publish = AsyncMock(
        side_effect=PublishError(
            [
                PublishViolation(
                    "DRAFT_REVISION_CONFLICT",
                    "Draft changed while publishing.",
                )
            ]
        )
    )
    response = client.post(f"/api/published-agents/{agent['id']}/releases")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "draft_revision_conflict"


def test_list_agents_only_returns_own():
    client_a, service, owner_a = _make_client()
    client_b, _, owner_b = _make_client(str(uuid4()))
    # Seed an agent for A through A's service, and one for B through B's service.
    # Since each client has its own service, simulate B's agent by creating
    # directly in B's own service isn't possible here — instead verify A only
    # sees A's agents.
    client_a.post("/api/published-agents", json={"slug": "a1", "display_name": "A1"})
    listing = client_a.get("/api/published-agents")
    assert listing.status_code == 200
    assert {a["slug"] for a in listing.json()} == {"a1"}


def test_patch_draft_updates_fields():
    client, _, _ = _make_client()
    agent = client.post("/api/published-agents", json={"slug": "bot", "display_name": "Bot"}).json()
    patched = client.patch(
        f"/api/published-agents/{agent['id']}/draft",
        json={"revision": 1, "soul_markdown": "# Soul", "model_name": "gpt-x"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["revision"] == 2
    assert patched.json()["soul_markdown"] == "# Soul"


def test_patch_draft_explicit_null_clears_model_but_omission_preserves_it():
    client, _, _ = _make_client()
    agent = client.post("/api/published-agents", json={"slug": "bot", "display_name": "Bot"}).json()
    set_model = client.patch(
        f"/api/published-agents/{agent['id']}/draft",
        json={"revision": 1, "model_name": "gpt-x"},
    )
    assert set_model.status_code == 200, set_model.text

    omitted = client.patch(
        f"/api/published-agents/{agent['id']}/draft",
        json={"revision": 2, "soul_markdown": "# keep model"},
    )
    assert omitted.status_code == 200, omitted.text
    assert omitted.json()["model_name"] == "gpt-x"

    cleared = client.patch(
        f"/api/published-agents/{agent['id']}/draft",
        json={"revision": 3, "model_name": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["model_name"] is None


def test_patch_draft_revision_conflict_returns_409():
    client, _, _ = _make_client()
    agent = client.post("/api/published-agents", json={"slug": "bot", "display_name": "Bot"}).json()
    client.patch(f"/api/published-agents/{agent['id']}/draft", json={"revision": 1, "soul_markdown": "# v1"})
    stale = client.patch(f"/api/published-agents/{agent['id']}/draft", json={"revision": 1, "soul_markdown": "# stale"})
    assert stale.status_code == 409


def test_patch_draft_unknown_skill_returns_422():
    client, _, _ = _make_client()
    agent = client.post("/api/published-agents", json={"slug": "bot", "display_name": "Bot"}).json()
    res = client.patch(
        f"/api/published-agents/{agent['id']}/draft",
        json={"revision": 1, "skills": [{"skill_name": "ghost", "source": "public"}]},
    )
    assert res.status_code == 422


def test_patch_draft_other_owners_connector_returns_422():
    client, _, _ = _make_client()
    agent = client.post("/api/published-agents", json={"slug": "bot", "display_name": "Bot"}).json()
    res = client.patch(
        f"/api/published-agents/{agent['id']}/draft",
        json={"revision": 1, "connector_grants": [{"connector_instance_id": "conn_other", "capability": "database.query"}]},
    )
    assert res.status_code == 422


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"skills": [{}]},
        {"skills": [{"skill_name": ""}]},
        {"skills": [{"skill_name": "reporting", "unexpected": "x"}]},
        {"skills": [{"skill_name": "reporting"}, {"skill_name": "reporting"}]},
        {"connector_grants": [{}]},
        {"connector_grants": [{"connector_instance_id": "", "capability": "database.query"}]},
        {"connector_grants": [{"connector_instance_id": "conn_own", "capability": ""}]},
        {"connector_grants": [{"connector_instance_id": "conn_own", "capability": "database.query", "unexpected": "x"}]},
        {
            "connector_grants": [
                {"connector_instance_id": "conn_own", "capability": "database.query"},
                {"connector_instance_id": "conn_own", "capability": "database.query"},
            ]
        },
    ],
)
def test_patch_draft_rejects_malformed_or_duplicate_nested_entries(invalid_fields):
    client, _, _ = _make_client()
    agent = client.post("/api/published-agents", json={"slug": "bot", "display_name": "Bot"}).json()

    response = client.patch(
        f"/api/published-agents/{agent['id']}/draft",
        json={"revision": 1, **invalid_fields},
    )

    assert response.status_code == 422
    draft = client.get(f"/api/published-agents/{agent['id']}").json()["draft"]
    assert draft["revision"] == 1
    assert draft["skills"] == []
    assert draft["connector_grants"] == []


def test_archive_suspend_resume():
    client, service, _ = _make_client()
    agent = client.post("/api/published-agents", json={"slug": "bot", "display_name": "Bot"}).json()
    draft_suspend = client.post(f"/api/published-agents/{agent['id']}/suspend")
    assert draft_suspend.status_code == 409
    assert draft_suspend.json()["detail"]["code"] == "invalid_state_transition"
    assert client.post(f"/api/published-agents/{agent['id']}/resume").status_code == 409

    service._agents.rows[agent["id"]]["current_release_id"] = "rel-1"
    service._agents.rows[agent["id"]]["status"] = "published"
    assert client.post(f"/api/published-agents/{agent['id']}/suspend").status_code == 200
    assert client.get(f"/api/published-agents/{agent['id']}").json()["status"] == "suspended"
    assert client.post(f"/api/published-agents/{agent['id']}/resume").status_code == 200
    assert client.post(f"/api/published-agents/{agent['id']}/archive").status_code == 200
    assert client.get(f"/api/published-agents/{agent['id']}").json()["status"] == "archived"


@pytest.mark.anyio
async def test_lifecycle_router_uses_real_repository_transition_guards(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'router-lifecycle.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    agents = PublishedAgentRepository(session_factory)
    service = DraftService(
        published_agent_repo=agents,
        draft_repo=AgentDraftRepository(session_factory),
        skills_index=_MemSkillsIndex(),
        connector_repo=_MemConnectorRepo(),
    )
    app = FastAPI()
    app.add_middleware(_SessionMiddleware, user_id="owner-a")
    app.include_router(published_agents.router)
    app.dependency_overrides[published_agents.get_draft_service] = lambda: service

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            created = (
                await client.post(
                    "/api/published-agents",
                    json={"slug": "real-state", "display_name": "Real state"},
                )
            ).json()
            agent_id = created["id"]

            assert (await client.post(f"/api/published-agents/{agent_id}/suspend")).status_code == 409
            assert (await client.post(f"/api/published-agents/{agent_id}/resume")).status_code == 409

            assert await agents.set_current_release(
                agent_id,
                owner_user_id="owner-a",
                release_id="rel-1",
            )
            suspended = await client.post(f"/api/published-agents/{agent_id}/suspend")
            assert suspended.status_code == 200
            assert suspended.json()["status"] == "suspended"
            resumed = await client.post(f"/api/published-agents/{agent_id}/resume")
            assert resumed.status_code == 200
            assert resumed.json()["status"] == "published"
            archived = await client.post(f"/api/published-agents/{agent_id}/archive")
            assert archived.status_code == 200
            assert archived.json()["status"] == "archived"
            assert (await client.post(f"/api/published-agents/{agent_id}/resume")).status_code == 409
    finally:
        await engine.dispose()


def test_draft_sandbox_run_freezes_revision_and_is_not_published_usage(
    monkeypatch,
):
    client, service, _ = _make_client()
    agent = client.post(
        "/api/published-agents",
        json={"slug": "sandbox-bot", "display_name": "Sandbox Bot"},
    ).json()
    saved = client.patch(
        f"/api/published-agents/{agent['id']}/draft",
        json={
            "revision": 1,
            "agent_markdown": "UNPUBLISHED SNAPSHOT INSTRUCTION",
            "model_name": "model-a",
            "connector_grants": [
                {
                    "connector_instance_id": "conn_own",
                    "capability": "database.query",
                }
            ],
        },
    ).json()
    captured: dict[str, Any] = {}

    async def fake_start_run(
        body,
        thread_id,
        request,
        *,
        draft_sandbox_context=None,
        **_kwargs,
    ):
        captured.update(
            body=body,
            thread_id=thread_id,
            request=request,
            context=draft_sandbox_context,
        )
        return SimpleNamespace(
            run_id="run-sandbox-1",
            thread_id=thread_id,
            status=SimpleNamespace(value="pending"),
        )

    monkeypatch.setattr(published_agents, "start_run", fake_start_run)
    before_status = service._agents.rows[agent["id"]]["status"]
    before_release = service._agents.rows[agent["id"]]["current_release_id"]

    response = client.post(
        f"/api/published-agents/{agent['id']}/draft/sandbox-runs",
        json={"message": "Follow the current draft."},
    )

    assert response.status_code == 202, response.text
    assert response.json() == {
        "agent_id": agent["id"],
        "thread_id": captured["thread_id"],
        "run_id": "run-sandbox-1",
        "status": "pending",
        "draft_revision": saved["revision"],
        "billable": False,
    }
    assert captured["context"].draft_revision == saved["revision"]
    assert captured["context"].agent_markdown == "UNPUBLISHED SNAPSHOT INSTRUCTION"
    assert captured["context"].connector_capabilities == (("conn_own", "database.query"),)
    assert captured["context"].connector_capability_map() == {"conn_own": ["database.query"]}
    assert captured["body"].metadata["draft_sandbox"] is True
    assert captured["body"].metadata["agent_name"] == agent["slug"]
    assert captured["body"].metadata["agent_display_name"] == "Sandbox Bot"
    assert "published_quota_reservation_id" not in captured["body"].metadata
    assert service._agents.rows[agent["id"]]["status"] == before_status
    assert service._agents.rows[agent["id"]]["current_release_id"] == before_release


def test_get_draft_sandbox_thread_returns_only_frozen_capability_scope():
    client, _, _ = _make_client(owner="owner-a")
    agent = client.post(
        "/api/published-agents",
        json={"slug": "scope-agent", "display_name": "Scope Agent"},
    ).json()
    draft = client.patch(
        f"/api/published-agents/{agent['id']}/draft",
        json={
            "revision": 1,
            "skills": [
                {"skill_name": "public-tool", "source": "public"},
            ],
            "connector_grants": [],
        },
    ).json()

    class ThreadStore:
        async def get(self, thread_id):
            assert thread_id == "thread-sandbox"
            return {
                "thread_id": thread_id,
                "metadata": {
                    "draft_sandbox": True,
                    "draft_sandbox_agent_id": agent["id"],
                    "draft_sandbox_revision": draft["revision"],
                    "draft_sandbox_billable": False,
                },
            }

    client.app.state.thread_store = ThreadStore()

    response = client.get("/api/published-agents/draft/sandbox-threads/thread-sandbox")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "agent_id": agent["id"],
        "agent_slug": "scope-agent",
        "thread_id": "thread-sandbox",
        "draft_revision": draft["revision"],
        "skill_names": ["public-tool"],
        "connector_ids": [],
        "billable": False,
    }


def test_patch_draft_revision_conflict_leaves_subtables_unchanged():
    """Regression (code-review Critical-3): a 409 PATCH must not have partially
    written skills/connector_grants. The stale-revision PATCH below should leave
    the draft's sub-tables in their pre-PATCH state."""
    client, _, _ = _make_client()
    agent = client.post("/api/published-agents", json={"slug": "bot", "display_name": "Bot"}).json()
    # Successful first PATCH sets skills + bumps revision to 2.
    client.patch(
        f"/api/published-agents/{agent['id']}/draft",
        json={"revision": 1, "skills": [{"skill_name": "reporting", "source": "public"}]},
    )
    draft_before = client.get(f"/api/published-agents/{agent['id']}").json()["draft"]
    skills_before = {s["skill_name"] for s in draft_before["skills"]}

    # Stale-revision PATCH (revision 1 again) that would change skills + grants.
    stale = client.patch(
        f"/api/published-agents/{agent['id']}/draft",
        json={
            "revision": 1,
            "skills": [{"skill_name": "public-tool", "source": "public"}],
            "connector_grants": [{"connector_instance_id": "conn_own", "capability": "database.query"}],
        },
    )
    assert stale.status_code == 409
    draft_after = client.get(f"/api/published-agents/{agent['id']}").json()["draft"]
    # Sub-tables must be unchanged by the rejected PATCH.
    assert {s["skill_name"] for s in draft_after["skills"]} == skills_before
    assert draft_after["connector_grants"] == []
    # And the revision must not have advanced.
    assert draft_after["revision"] == draft_before["revision"]


def test_unknown_agent_returns_404():
    client, _, _ = _make_client()
    assert client.get("/api/published-agents/pa_missing").status_code == 404
    assert client.patch("/api/published-agents/pa_missing/draft", json={"revision": 1, "soul_markdown": "x"}).status_code == 404


def test_list_releases_unknown_or_cross_owner_agent_returns_404():
    """Regression (code-review Important-4): missing/cross-owner agent must 404, not return []."""
    client, _, _ = _make_client()
    # Agent does not exist for this owner.
    assert client.get("/api/published-agents/pa_missing/releases").status_code == 404


def test_non_session_auth_rejected():
    owner = str(uuid4())
    app = FastAPI()
    app.add_middleware(_SessionMiddleware, user_id=owner, auth_method="api_key")
    app.include_router(published_agents.router)
    app.dependency_overrides[published_agents.get_draft_service] = lambda: _build_service(owner)
    client = TestClient(app)
    assert client.get("/api/published-agents").status_code == 401
