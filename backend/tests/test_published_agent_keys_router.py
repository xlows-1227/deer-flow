from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.gateway.routers import published_agent_keys


class _AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, user_id: str = "owner-a", auth_method: str = "session") -> None:
        super().__init__(app)
        self.user_id = user_id
        self.auth_method = auth_method

    async def dispatch(self, request: Request, call_next):
        request.state.user = SimpleNamespace(id=self.user_id)
        request.state.auth_method = self.auth_method
        return await call_next(request)


class _DraftService:
    async def get_agent(self, agent_id: str, *, owner_user_id: str):
        if (agent_id, owner_user_id) == ("pa_owned", "owner-a"):
            return {"id": agent_id, "owner_user_id": owner_user_id, "status": "published"}
        return None


class _KeyRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.serial = 0

    async def create(self, *, agent_id: str, name: str, quota_overrides: dict | None = None):
        self.serial += 1
        key_id = f"{self.serial:032x}"
        plaintext = f"dfa_{key_id}_{'s' * 40}{self.serial:03d}"
        row = {
            "id": key_id,
            "agent_id": agent_id,
            "name": name,
            "key_prefix": f"dfa_{key_id[:8]}",
            "last_four": plaintext[-4:],
            "status": "active",
            "quota_overrides": quota_overrides or {},
            "created_at": datetime(2026, 7, 14, tzinfo=UTC),
            "last_used_at": None,
            "expires_at": None,
            "revoked_at": None,
            "rotation_of": None,
        }
        self.rows[key_id] = row
        return {**row, "api_key": plaintext}

    async def list_by_agent(self, agent_id: str):
        return [dict(row) for row in self.rows.values() if row["agent_id"] == agent_id]

    async def rotate(self, agent_id: str, key_id: str, *, overlap_seconds: int):  # noqa: ARG002
        old = self.rows.get(key_id)
        if old is None or old["agent_id"] != agent_id:
            return None
        created = await self.create(agent_id=agent_id, name=old["name"], quota_overrides=old["quota_overrides"])
        created["rotation_of"] = key_id
        self.rows[created["id"]]["rotation_of"] = key_id
        return created

    async def revoke(self, agent_id: str, key_id: str):
        row = self.rows.get(key_id)
        if row is None or row["agent_id"] != agent_id:
            return False
        row["status"] = "revoked"
        return True

    async def update(self, agent_id: str, key_id: str, *, name=None, quota_overrides=None):
        row = self.rows.get(key_id)
        if row is None or row["agent_id"] != agent_id:
            return None
        if name is not None:
            row["name"] = name
        if quota_overrides is not None:
            row["quota_overrides"] = quota_overrides
        return dict(row)


def _client(*, auth_method: str = "session") -> tuple[TestClient, _KeyRepo]:
    app = FastAPI()
    app.add_middleware(_AuthMiddleware, auth_method=auth_method)
    app.include_router(published_agent_keys.router)
    repo = _KeyRepo()
    app.dependency_overrides[published_agent_keys.get_agent_api_key_repo] = lambda: repo
    app.dependency_overrides[published_agent_keys.get_draft_service] = lambda: _DraftService()
    return TestClient(app), repo


def test_create_plaintext_is_returned_once_and_list_is_safe() -> None:
    client, _repo = _client()

    created = client.post("/api/published-agents/pa_owned/keys", json={"name": "Production"})
    listed = client.get("/api/published-agents/pa_owned/keys")

    assert created.status_code == 201, created.text
    assert created.json()["api_key"].startswith("dfa_")
    assert created.json()["warning"] == "This API key will not be shown again."
    assert listed.status_code == 200
    assert "api_key" not in listed.text
    assert "secret" not in listed.text
    assert listed.json()[0]["name"] == "Production"


def test_non_owner_gets_not_found() -> None:
    client, _repo = _client()
    assert client.get("/api/published-agents/pa_other/keys").status_code == 404
    assert client.post("/api/published-agents/pa_other/keys", json={"name": "Nope"}).status_code == 404


def test_rotate_patch_and_revoke() -> None:
    client, _repo = _client()
    created = client.post("/api/published-agents/pa_owned/keys", json={"name": "Old"}).json()
    key_id = created["id"]

    patched = client.patch(
        f"/api/published-agents/pa_owned/keys/{key_id}",
        json={"name": "New", "quota_overrides": {"daily_runs": 5}},
    )
    rotated = client.post(f"/api/published-agents/pa_owned/keys/{key_id}/rotate")
    revoked = client.post(f"/api/published-agents/pa_owned/keys/{key_id}/revoke")

    assert patched.status_code == 200
    assert patched.json()["quota_overrides"] == {"daily_runs": 5}
    assert rotated.status_code == 201
    assert rotated.json()["rotation_of"] == key_id
    assert rotated.json()["api_key"].startswith("dfa_")
    assert revoked.status_code == 200
    assert revoked.json() == {"revoked": True}


def test_agent_key_cannot_call_owner_management_api() -> None:
    client, _repo = _client(auth_method="agent_api_key")
    response = client.post("/api/published-agents/pa_owned/keys", json={"name": "Escalate"})
    assert response.status_code == 401
