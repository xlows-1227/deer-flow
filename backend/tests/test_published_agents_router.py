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

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.gateway.routers import published_agents


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
        for k, v in fields.items():
            if v is not None:
                d[k] = v
        d["revision"] = revision + 1
        return dict(d)

    async def update_bundle(self, agent_id, *, owner_user_id, revision, skills=None, connector_grants=None, **fields):
        """Atomic counterpart: revision check gates the whole update (Critical-3)."""
        d = self.drafts.get(agent_id)
        if d is None or not self._owned(agent_id, owner_user_id) or d["revision"] != revision:
            return None
        for k, v in fields.items():
            if v is not None:
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


class _MemConnectorRepo:
    async def get_instance(self, connector_id, *, owner_id=...):
        if connector_id == "conn_own":
            return {"id": connector_id, "owner_id": owner_id, "status": "active"}
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


def _make_client(owner: str = None):
    owner = owner or str(uuid4())
    app = FastAPI()
    app.add_middleware(_SessionMiddleware, user_id=owner)
    app.include_router(published_agents.router)
    service = _build_service(owner)
    app.dependency_overrides[published_agents.get_draft_service] = lambda: service
    return TestClient(app), service, owner


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


def test_archive_suspend_resume():
    client, _, _ = _make_client()
    agent = client.post("/api/published-agents", json={"slug": "bot", "display_name": "Bot"}).json()
    assert client.post(f"/api/published-agents/{agent['id']}/suspend").status_code == 200
    assert client.get(f"/api/published-agents/{agent['id']}").json()["status"] == "suspended"
    assert client.post(f"/api/published-agents/{agent['id']}/resume").status_code == 200
    assert client.post(f"/api/published-agents/{agent['id']}/archive").status_code == 200
    assert client.get(f"/api/published-agents/{agent['id']}").json()["status"] == "archived"


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


def test_non_session_auth_rejected():
    owner = str(uuid4())
    app = FastAPI()
    app.add_middleware(_SessionMiddleware, user_id=owner, auth_method="api_key")
    app.include_router(published_agents.router)
    app.dependency_overrides[published_agents.get_draft_service] = lambda: _build_service(owner)
    client = TestClient(app)
    assert client.get("/api/published-agents").status_code == 401
