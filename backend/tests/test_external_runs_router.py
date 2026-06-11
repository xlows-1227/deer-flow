from types import SimpleNamespace

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.gateway.deps import get_config, get_external_conversation_repo, get_external_idempotency_repo
from app.gateway.routers import external
from deerflow.runtime import DisconnectMode, RunRecord, RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore


class StateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.user = SimpleNamespace(id="alice")
        request.state.api_key_id = "key-1"
        request.state.allowed_skills = ["sales-report"]
        request.state.external_scopes = [
            "external:runs:create",
            "external:runs:read",
            "external:runs:cancel",
        ]
        return await call_next(request)


class ConversationRepo:
    async def get(self, conversation_id, *, user_id):
        if conversation_id != "conv-1" or user_id != "alice":
            return None
        return {
            "conversation_id": "conv-1",
            "user_id": "alice",
            "thread_id": "thread-1",
            "agent_id": "lead_agent",
            "default_skill_name": "sales-report",
            "source": "crm",
            "status": "active",
        }


class IdempotencyRepo:
    async def get(self, **kwargs):
        return None

    async def put(self, values):
        return values


class ClaimingIdempotencyRepo:
    def __init__(self):
        self.released = False

    async def claim(self, values):
        return values, True

    async def release(self, **kwargs):
        self.released = True


class Manager:
    def __init__(self, record):
        self.record = record

    async def get(self, run_id, *, user_id=None):
        return self.record if run_id == self.record.run_id and user_id == "alice" else None

    async def cancel(self, run_id):
        self.record.status = RunStatus.interrupted
        return True


def _make_client(monkeypatch):
    store = MemoryRunStore()
    record = RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id="lead_agent",
        status=RunStatus.pending,
        on_disconnect=DisconnectMode.continue_,
        metadata={
            "external_api": True,
            "external_conversation_id": "conv-1",
            "skill_name": "sales-report",
        },
        created_at="2026-06-08T00:00:00Z",
        updated_at="2026-06-08T00:00:00Z",
    )

    async def start_run(body, thread_id, request):
        assert thread_id == "thread-1"
        assert body.context["skill_name"] == "sales-report"
        assert body.context["external_allowed_skills"] == ["sales-report"]
        assert body.context["mode"] == "pro"
        assert body.context["thinking_enabled"] is True
        assert body.context["is_plan_mode"] is True
        assert body.context["subagent_enabled"] is False
        assert body.context["reasoning_effort"] == "medium"
        assert body.assistant_id == "lead_agent"
        await store.put(
            record.run_id,
            thread_id=record.thread_id,
            user_id="alice",
            status=record.status.value,
            metadata=record.metadata,
            created_at=record.created_at,
        )
        return record

    monkeypatch.setattr(external, "start_run", start_run)
    monkeypatch.setattr(external, "require_external_skill", lambda **kwargs: object())
    monkeypatch.setattr(external, "get_external_api_config", lambda: SimpleNamespace(active_run_limit_per_user=3))
    app = FastAPI()
    app.state.run_store = store
    app.state.run_manager = Manager(record)
    app.add_middleware(StateMiddleware)
    app.include_router(external.router)
    app.dependency_overrides[get_external_conversation_repo] = lambda: ConversationRepo()
    app.dependency_overrides[get_external_idempotency_repo] = lambda: IdempotencyRepo()
    app.dependency_overrides[get_config] = lambda: object()
    return TestClient(app), record


def test_create_read_and_cancel_external_run(monkeypatch):
    client, record = _make_client(monkeypatch)
    created = client.post("/api/v1/external/conversations/conv-1/runs", json={"message": "Create report"})
    assert created.status_code == 202
    assert created.json()["conversation_id"] == "conv-1"
    assert created.json()["skill"] == "sales-report"
    assert "thread_id" not in created.json()

    fetched = client.get("/api/v1/external/runs/run-1")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "pending"

    cancelled = client.post("/api/v1/external/runs/run-1/cancel")
    assert cancelled.status_code == 200
    assert record.status == RunStatus.interrupted


def test_selected_skill_rejects_flash(monkeypatch):
    client, _ = _make_client(monkeypatch)
    response = client.post(
        "/api/v1/external/conversations/conv-1/runs",
        json={"message": "Create report", "mode": "flash"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "flash_not_available_with_skill"


def test_external_run_owner_isolation(monkeypatch):
    client, _ = _make_client(monkeypatch)
    response = client.get("/api/v1/external/runs/unknown")
    assert response.status_code == 404


def test_concurrency_limit_releases_idempotency_claim(monkeypatch):
    client, _ = _make_client(monkeypatch)
    repository = ClaimingIdempotencyRepo()
    client.app.dependency_overrides[get_external_idempotency_repo] = lambda: repository
    monkeypatch.setattr(external, "get_external_api_config", lambda: SimpleNamespace(active_run_limit_per_user=0))

    response = client.post(
        "/api/v1/external/conversations/conv-1/runs",
        headers={"Idempotency-Key": "run-1"},
        json={"message": "Create report"},
    )

    assert response.status_code == 429
    assert repository.released is True


def test_run_idempotency_hash_includes_conversation_id():
    body = {"message": "Create report", "skill": None, "mode": "standard", "metadata": {}}
    assert external._request_hash("create_run:conv-1", body) != external._request_hash("create_run:conv-2", body)


def test_external_run_modes_map_to_runtime_flags():
    assert external._run_context(mode="thinking", skill_name=None, allowed_skills=["sales-report"]) == {
        "skill_name": None,
        "external_allowed_skills": ["sales-report"],
        "mode": "thinking",
        "thinking_enabled": True,
        "is_plan_mode": False,
        "subagent_enabled": False,
        "reasoning_effort": "low",
    }
    assert external._run_context(mode="ultra", skill_name=None, allowed_skills=[])["subagent_enabled"] is True
    assert external._run_context(mode="flash", skill_name=None, allowed_skills=[])["thinking_enabled"] is False


def test_external_run_response_does_not_expose_internal_error():
    response = external._run_response(
        {
            "run_id": "run-1",
            "status": "error",
            "metadata": {"external_conversation_id": "conv-1"},
            "error": "password=secret at D:\\internal\\db.py",
        }
    )
    assert response.error == "The run failed."
