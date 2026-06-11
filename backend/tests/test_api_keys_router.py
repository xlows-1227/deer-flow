from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.gateway.external.config import ExternalAPIConfig, set_external_api_config
from app.gateway.routers import api_keys


class UserMiddleware(BaseHTTPMiddleware):
    user_id = uuid4()

    async def dispatch(self, request: Request, call_next):
        request.state.user = SimpleNamespace(id=self.user_id)
        request.state.auth_method = request.headers.get("X-Test-Auth-Method", "session")
        return await call_next(request)


class MemoryKeyRepository:
    def __init__(self):
        self.rows = {}
        self.current = {}

    async def rotate(self, values):
        old = self.current.get(values["user_id"])
        if old:
            self.rows[old]["status"] = "revoked"
        row = {**values, "status": "active", "created_at": "2026-06-08T00:00:00Z", "last_used_at": None}
        self.rows[values["id"]] = row
        self.current[values["user_id"]] = values["id"]
        return row

    async def get_current_for_user(self, user_id):
        key_id = self.current.get(user_id)
        row = self.rows.get(key_id)
        return row if row and row["status"] == "active" else None

    async def update_policy(self, user_id, allowed_skills):
        row = await self.get_current_for_user(user_id)
        if row:
            row["allowed_skills"] = allowed_skills
        return row

    async def revoke(self, user_id, reason="revoked"):
        row = await self.get_current_for_user(user_id)
        if not row:
            return False
        row["status"] = "revoked"
        return True


def _make_client():
    repository = MemoryKeyRepository()
    app = FastAPI()
    app.add_middleware(UserMiddleware)
    app.include_router(api_keys.router)
    app.dependency_overrides[api_keys.get_api_key_repo] = lambda: repository
    app.dependency_overrides[api_keys.get_config] = lambda: object()
    api_keys.get_or_new_skill_storage = lambda app_config: SimpleNamespace(
        load_skills=lambda enabled_only: [
            SimpleNamespace(name="sales-report"),
            SimpleNamespace(name="a-skill"),
            SimpleNamespace(name="z-skill"),
        ]
    )
    set_external_api_config(ExternalAPIConfig(api_key_pepper="p" * 32))
    return TestClient(app), repository


def test_api_key_lifecycle_never_reveals_plaintext_after_rotate():
    client, _ = _make_client()
    created = client.post("/api/v1/api-keys/current/rotate", json={"allowed_skills": ["sales-report"]})
    assert created.status_code == 201
    assert created.json()["api_key"].startswith("dfk_")

    current = client.get("/api/v1/api-keys/current")
    assert current.status_code == 200
    assert current.json()["exists"] is True
    assert "api_key" not in current.json()
    assert current.json()["allowed_skills"] == ["sales-report"]


def test_api_key_management_rejects_non_session_authentication():
    client, _ = _make_client()
    response = client.get("/api/v1/api-keys/current", headers={"X-Test-Auth-Method": "internal"})
    assert response.status_code == 401


def test_api_key_policy_update_and_revoke_are_stable():
    client, _ = _make_client()
    client.post("/api/v1/api-keys/current/rotate")
    updated = client.put("/api/v1/api-keys/current/policy", json={"allowed_skills": ["z-skill", "a-skill"]})
    assert updated.status_code == 200
    assert updated.json()["allowed_skills"] == ["a-skill", "z-skill"]
    assert client.delete("/api/v1/api-keys/current").json() == {"revoked": True}
    assert client.delete("/api/v1/api-keys/current").json() == {"revoked": True}


def test_api_key_policy_rejects_disabled_or_missing_skill():
    client, _ = _make_client()
    response = client.post("/api/v1/api-keys/current/rotate", json={"allowed_skills": ["missing-skill"]})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "skill_not_available"
