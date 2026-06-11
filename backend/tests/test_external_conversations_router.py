from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, Request
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.gateway.deps import (
    get_checkpointer,
    get_config,
    get_external_conversation_repo,
    get_external_idempotency_repo,
    get_thread_store,
)
from app.gateway.routers import external
from deerflow.persistence.external_conversation import ExternalConversationExistsError
from deerflow.persistence.external_idempotency import IdempotencyConflictError
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore

USER_ID = str(uuid4())


class ExternalStateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.user = SimpleNamespace(id=USER_ID)
        request.state.api_key_id = "key-1"
        request.state.external_scopes = [
            "external:skills:read",
            "external:conversations:create",
            "external:conversations:read",
        ]
        request.state.allowed_skills = ["sales-report"]
        return await call_next(request)


class ConversationRepo:
    def __init__(self):
        self.rows = {}

    async def create(self, values):
        for row in self.rows.values():
            if values.get("external_conversation_id") and row["user_id"] == values["user_id"] and row["source"] == values["source"] and row["external_conversation_id"] == values["external_conversation_id"]:
                raise ExternalConversationExistsError(row["conversation_id"])
        row = {
            **values,
            "status": "active",
            "created_at": "2026-06-08T00:00:00Z",
            "updated_at": "2026-06-08T00:00:00Z",
        }
        self.rows[row["conversation_id"]] = row
        return row

    async def get(self, conversation_id, *, user_id):
        row = self.rows.get(conversation_id)
        return row if row and row["user_id"] == user_id else None


class IdempotencyRepo:
    def __init__(self):
        self.rows = {}

    async def get(self, *, api_key_id, idempotency_key, request_hash):
        row = self.rows.get((api_key_id, idempotency_key))
        if row and row["request_hash"] != request_hash:
            raise IdempotencyConflictError()
        return row

    async def put(self, values):
        self.rows[(values["api_key_id"], values["idempotency_key"])] = values
        return values


def _make_client(monkeypatch):
    conversations = ConversationRepo()
    idempotency = IdempotencyRepo()
    app = FastAPI()
    app.state.checkpointer = InMemorySaver()
    app.state.thread_store = MemoryThreadMetaStore(InMemoryStore())
    app.add_middleware(ExternalStateMiddleware)
    app.include_router(external.router)
    app.dependency_overrides[get_external_conversation_repo] = lambda: conversations
    app.dependency_overrides[get_external_idempotency_repo] = lambda: idempotency
    app.dependency_overrides[get_checkpointer] = lambda: app.state.checkpointer
    app.dependency_overrides[get_thread_store] = lambda: app.state.thread_store
    app.dependency_overrides[get_config] = lambda: object()
    skill = SimpleNamespace(
        name="sales-report",
        description="Sales",
        display_name="Sales Report",
        description_zh="销售报告",
    )
    monkeypatch.setattr(external, "available_external_skills", lambda **kwargs: [skill])
    monkeypatch.setattr(external, "require_external_skill", lambda **kwargs: skill)
    return TestClient(app), conversations


def test_create_get_and_idempotent_replay_never_expose_thread_id(monkeypatch):
    client, conversations = _make_client(monkeypatch)
    body = {
        "source": "crm",
        "external_conversation_id": "crm-1",
        "default_skill": "sales-report",
    }
    first = client.post("/api/v1/external/conversations", json=body, headers={"Idempotency-Key": "create-1"})
    second = client.post("/api/v1/external/conversations", json=body, headers={"Idempotency-Key": "create-1"})
    assert first.status_code == 201
    assert second.json()["conversation_id"] == first.json()["conversation_id"]
    assert "thread_id" not in first.json()
    assert len(conversations.rows) == 1
    fetched = client.get(f"/api/v1/external/conversations/{first.json()['conversation_id']}")
    assert fetched.status_code == 200
    assert "thread_id" not in fetched.json()


def test_idempotency_conflict_and_external_mapping_conflict(monkeypatch):
    client, _ = _make_client(monkeypatch)
    first = {"source": "crm", "external_conversation_id": "crm-1"}
    assert client.post("/api/v1/external/conversations", json=first, headers={"Idempotency-Key": "same"}).status_code == 201
    conflict = client.post(
        "/api/v1/external/conversations",
        json={"source": "crm", "external_conversation_id": "crm-2"},
        headers={"Idempotency-Key": "same"},
    )
    assert conflict.status_code == 409
    duplicate = client.post("/api/v1/external/conversations", json=first)
    assert duplicate.status_code == 409


def test_list_skills_returns_summary_only(monkeypatch):
    client, _ = _make_client(monkeypatch)
    response = client.get("/api/v1/external/skills")
    assert response.status_code == 200
    assert response.json()["skills"] == [
        {
            "name": "sales-report",
            "description": "Sales",
            "display_name": "Sales Report",
            "description_zh": "销售报告",
        }
    ]
