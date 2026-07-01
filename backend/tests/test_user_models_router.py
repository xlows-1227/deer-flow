from __future__ import annotations

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.routers import user_models


class _FakeUserModelService:
    async def list_models(self, user_id: str):  # noqa: ARG002
        return [
            {
                "id": "umodel_1",
                "user_id": user_id,
                "name": "router-model",
                "display_name": "Router Model",
                "provider": "openai",
                "model": "gpt-4o",
                "base_url": "https://api.openai.com/v1",
                "enabled": True,
                "has_api_key": True,
                "api_key_last_four": "1234",
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def create_model(self, user_id: str, payload):  # noqa: ARG002
        return {
            "id": "umodel_1",
            "user_id": user_id,
            "name": payload.name,
            "display_name": payload.display_name,
            "provider": payload.provider,
            "model": payload.model,
            "base_url": payload.base_url,
            "enabled": payload.enabled,
            "has_api_key": bool(payload.api_key),
            "api_key_last_four": "1234",
            "created_at": None,
            "updated_at": None,
        }


def test_custom_models_router_masks_api_key(monkeypatch):
    monkeypatch.setattr(user_models, "make_user_model_service", lambda: _FakeUserModelService())
    app = make_authed_test_app()
    app.include_router(user_models.router)
    from app.gateway.routers import models

    app.include_router(models.router)

    with TestClient(app) as client:
        list_response = client.get("/api/models/custom")
        assert list_response.status_code == 200, list_response.text
        assert list_response.json()["models"][0]["name"] == "router-model"

        create_response = client.post(
            "/api/models/custom",
            json={
                "name": "router-model",
                "provider": "openai",
                "model": "gpt-4o",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-router-secret-key",
            },
        )
        assert create_response.status_code == 201, create_response.text
        payload = create_response.json()
        assert payload["has_api_key"] is True
        assert "sk-router" not in create_response.text

        list_response = client.get("/api/models/custom")
        assert list_response.status_code == 200
        assert list_response.json()["models"][0]["name"] == "router-model"
        assert "sk-router" not in list_response.text

        model_detail = client.get("/api/models/custom")
        assert model_detail.status_code == 200
        assert "Model 'custom' not found" not in model_detail.text
