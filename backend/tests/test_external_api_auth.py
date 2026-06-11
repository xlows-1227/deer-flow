from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from app.gateway.api_key_auth import ExternalAPIAuthMiddleware
from app.gateway.auth.models import User
from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.csrf_middleware import CSRFMiddleware
from app.gateway.external.config import ExternalAPIConfig, set_external_api_config
from app.gateway.external.service import APIKeyService


class MemoryKeyRepository:
    def __init__(self):
        self.rows = {}

    async def rotate(self, values):
        row = {**values, "status": "active"}
        self.rows[values["id"]] = row
        return row

    async def get_active_by_id(self, key_id):
        row = self.rows.get(key_id)
        return row if row and row["status"] == "active" else None

    async def touch_last_used(self, key_id):
        self.rows[key_id]["touched"] = True


def _make_app(repository):
    app = FastAPI()
    app.state.api_key_repo = repository
    app.add_middleware(AuthMiddleware)
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(ExternalAPIAuthMiddleware)

    @app.post("/api/v1/external/test")
    async def external_test(request: Request):
        return {
            "user_id": str(request.state.user.id),
            "auth_method": request.state.auth_method,
            "api_key_id": request.state.api_key_id,
        }

    @app.get("/api/models")
    async def models():
        return {"models": []}

    return app


@pytest.fixture
def auth_setup(monkeypatch):
    set_external_api_config(ExternalAPIConfig(api_key_pepper="p" * 32))
    repository = MemoryKeyRepository()
    user = User(id=uuid4(), email="external@example.com")
    provider = SimpleNamespace(get_user=lambda _user_id: None)

    async def get_user(_user_id):
        return user

    provider.get_user = get_user
    monkeypatch.setattr("app.gateway.deps.get_local_provider", lambda: provider)
    yield repository, user
    set_external_api_config(None)


def test_external_api_requires_bearer_key(auth_setup):
    repository, _ = auth_setup
    response = TestClient(_make_app(repository)).post("/api/v1/external/test")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_api_key"


def test_valid_key_authenticates_and_skips_csrf(auth_setup):
    repository, user = auth_setup
    service = APIKeyService(repository)
    import asyncio

    key = asyncio.run(service.rotate(user_id=str(user.id), allowed_skills=["sales-report"]))
    response = TestClient(_make_app(repository)).post(
        "/api/v1/external/test",
        headers={"Authorization": f"Bearer {key['api_key']}"},
    )
    assert response.status_code == 200
    assert response.json()["auth_method"] == "api_key"
    assert response.json()["user_id"] == str(user.id)


def test_invalid_authorization_header_does_not_bypass_csrf_or_auth(auth_setup):
    repository, _ = auth_setup
    response = TestClient(_make_app(repository)).post(
        "/api/v1/external/test",
        headers={"Authorization": "Bearer invalid"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_api_key_cannot_authenticate_management_route(auth_setup):
    repository, user = auth_setup
    service = APIKeyService(repository)
    import asyncio

    key = asyncio.run(service.rotate(user_id=str(user.id)))
    response = TestClient(_make_app(repository)).get(
        "/api/models",
        headers={"Authorization": f"Bearer {key['api_key']}"},
    )
    assert response.status_code == 401


def test_external_api_fails_closed_without_sql_persistence():
    app = _make_app(None)
    response = TestClient(app).post("/api/v1/external/test", headers={"Authorization": "Bearer anything"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "external_api_unavailable"
