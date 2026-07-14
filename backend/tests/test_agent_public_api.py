from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.gateway.deps import (
    get_external_conversation_repo,
    get_external_idempotency_repo,
    get_published_agent_repo,
    get_published_agent_resolver,
    get_quota_ledger,
)
from app.gateway.external.agent_auth import AgentAPIAuthMiddleware
from app.gateway.external.agent_serialization import assert_public_payload_safe
from app.gateway.routers import agent_public_api
from deerflow.publishing.context import PublishedAgentContext
from deerflow.publishing.quota import (
    EffectiveQuota,
    QuotaExceededError,
    Reservation,
)
from deerflow.publishing.resolver import AgentNotAvailableError, AgentSuspendedError
from deerflow.runtime import DisconnectMode, RunStatus


def _context(agent_id: str = "pa_1", credential_id: str = "key_1") -> PublishedAgentContext:
    quota = EffectiveQuota(
        max_concurrent_runs=4,
        daily_runs=100,
        daily_tokens=100_000,
        max_run_seconds=60,
        max_tokens_per_run=1_000,
        max_input_bytes=32_000,
        inbound_rps=20,
    )
    return PublishedAgentContext(
        owner_user_id="owner-1",
        agent_id=agent_id,
        release_id="rel_1",
        source="api",
        credential_id=credential_id,
        external_actor=f"agent-key:{credential_id}",
        conversation_scope="conv_1",
        skill_revision_ids=(),
        connector_capabilities=(),
        tool_groups=(),
        model_name="test-model",
        instructions="trusted",
        effective_quota=quota,
        correlation_id="req_12345678",
        idempotency_key=None,
    )


class _KeyRepo:
    def __init__(self, row):
        self.row = row
        self.touched = []

    async def verify(self, credential):
        return self.row if credential == "valid" else None

    async def touch_last_used(self, key_id):
        self.touched.append(key_id)


class _AgentRepo:
    async def get_owner(self, agent_id):
        return "owner-1" if agent_id in {"pa_1", "pa_2"} else None


def test_agent_key_auth_rejects_cross_agent_as_not_found_and_touches_valid_key():
    app = FastAPI()
    app.add_middleware(AgentAPIAuthMiddleware)
    app.state.agent_api_key_repo = _KeyRepo({"id": "key_1", "agent_id": "pa_1"})
    app.state.published_agent_repo = _AgentRepo()

    @app.get("/api/v1/agents/{agent_id}")
    async def endpoint(agent_id: str, request: Request):
        return {"agent_id": agent_id, "owner": request.state.owner_user_id}

    client = TestClient(app)
    assert client.get("/api/v1/agents/pa_1").status_code == 401
    mismatch = client.get(
        "/api/v1/agents/pa_2",
        headers={"Authorization": "Bearer valid"},
    )
    assert mismatch.status_code == 404
    response = client.get(
        "/api/v1/agents/pa_1",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 200
    assert response.json() == {"agent_id": "pa_1", "owner": "owner-1"}
    assert app.state.agent_api_key_repo.touched == ["key_1"]


class _AllowLedger:
    async def reserve(self, context, *, request_key):
        return Reservation(
            id=f"qres_{request_key[:8]}",
            request_key=request_key,
            agent_id=context.agent_id,
            credential_id=context.credential_id,
            reserved_tokens=context.effective_quota.max_tokens_per_run,
            status="pending",
        )

    async def settle(self, *args, **kwargs):
        return True

    async def release(self, *args, **kwargs):
        return True


def _router_app(
    *,
    resolver,
    conversations,
    agents=None,
    idempotency=None,
    quota_ledger=None,
) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def trusted_state(request: Request, call_next):
        request.state.agent_key_id = request.headers.get("X-Credential", "key_1")
        request.state.owner_user_id = "owner-1"
        request.state.request_id = "req_12345678"
        return await call_next(request)

    app.include_router(agent_public_api.router)
    app.dependency_overrides[get_published_agent_resolver] = lambda: resolver
    app.dependency_overrides[get_external_conversation_repo] = lambda: conversations
    app.dependency_overrides[get_published_agent_repo] = lambda: agents or AsyncMock()
    app.dependency_overrides[get_external_idempotency_repo] = lambda: idempotency or AsyncMock()
    app.dependency_overrides[get_quota_ledger] = lambda: quota_ledger or _AllowLedger()
    return app


def test_metadata_is_explicitly_whitelisted_and_lifecycle_is_fail_closed():
    resolver = AsyncMock()
    resolver.resolve.return_value = _context()
    agents = AsyncMock()
    agents.get.return_value = {
        "id": "pa_1",
        "display_name": "Public Agent",
        "description": "Safe",
        "avatar_ref": "avatar.png",
        "owner_user_id": "owner-1",
        "current_release_id": "rel_secret",
    }
    app = _router_app(resolver=resolver, conversations=AsyncMock(), agents=agents)
    response = TestClient(app).get("/api/v1/agents/pa_1")
    assert response.status_code == 200
    assert response.json() == {
        "agent_id": "pa_1",
        "display_name": "Public Agent",
        "description": "Safe",
        "avatar": "avatar.png",
    }
    assert_public_payload_safe(response.json())

    resolver.resolve.side_effect = AgentSuspendedError("pa_1")
    assert TestClient(app).get("/api/v1/agents/pa_1").status_code == 410
    resolver.resolve.side_effect = AgentNotAvailableError("pa_1")
    assert TestClient(app).get("/api/v1/agents/pa_1").status_code == 404


def test_conversation_lookup_is_scoped_by_agent_and_credential():
    now = datetime.now(UTC)
    conversations = AsyncMock()

    async def get_for_agent(conversation_id, *, agent_id, credential_id):
        if (conversation_id, agent_id, credential_id) != ("conv_1", "pa_1", "key_1"):
            return None
        return {
            "conversation_id": "conv_1",
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }

    conversations.get_for_agent.side_effect = get_for_agent
    resolver = AsyncMock()
    resolver.resolve.return_value = _context()
    app = _router_app(resolver=resolver, conversations=conversations)
    client = TestClient(app)
    assert client.get("/api/v1/agents/pa_1/conversations/conv_1").status_code == 200
    assert (
        client.get(
            "/api/v1/agents/pa_1/conversations/conv_1",
            headers={"X-Credential": "key_2"},
        ).status_code
        == 404
    )


def test_public_run_body_cannot_select_model_release_skill_or_context():
    resolver = AsyncMock()
    resolver.resolve.return_value = _context()
    app = _router_app(resolver=resolver, conversations=AsyncMock())
    client = TestClient(app)
    path = "/api/v1/agents/pa_1/conversations/conv_1/runs"
    for injected in (
        {"model_name": "attacker-model"},
        {"release_id": "rel_attacker"},
        {"skill": "admin"},
        {"context": {"memory_enabled": True}},
    ):
        response = client.post(path, json={"message": "hello", **injected})
        assert response.status_code == 422


def test_run_idempotency_replays_one_internal_run(monkeypatch):
    now = datetime.now(UTC)
    conversation = {
        "conversation_id": "conv_1",
        "agent_id": "pa_1",
        "credential_id": "key_1",
        "thread_id": "thread_1",
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    conversations = AsyncMock()
    conversations.get_for_agent.return_value = conversation
    resolver = AsyncMock()
    resolver.resolve.return_value = _context()
    record = SimpleNamespace(
        run_id="run_1",
        thread_id="thread_1",
        status=RunStatus.pending,
        metadata={
            "published_agent_id": "pa_1",
            "published_credential_id": "key_1",
            "published_conversation_id": "conv_1",
        },
        last_ai_message=None,
        created_at=now,
        updated_at=now,
        task=None,
        on_disconnect=DisconnectMode.continue_,
    )
    start = AsyncMock(return_value=record)
    monkeypatch.setattr(agent_public_api, "start_run", start)

    class Idempotency:
        def __init__(self):
            self.response = None

        async def claim(self, values):
            if self.response is None:
                return {**values, "response_json": None}, True
            return {"response_json": self.response}, False

        async def complete(self, **values):
            self.response = values["response_json"]

        async def release(self, **values):
            raise AssertionError(values)

    idempotency = Idempotency()
    app = _router_app(
        resolver=resolver,
        conversations=conversations,
        idempotency=idempotency,
    )
    app.state.run_manager = SimpleNamespace(get=AsyncMock(return_value=record))
    client = TestClient(app)
    path = "/api/v1/agents/pa_1/conversations/conv_1/runs"
    headers = {"Idempotency-Key": "same-request"}
    first = client.post(path, json={"message": "hello"}, headers=headers)
    second = client.post(path, json={"message": "hello"}, headers=headers)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["run_id"] == second.json()["run_id"] == "run_1"
    start.assert_awaited_once()


def test_quota_rejection_returns_429_retry_after_and_creates_no_run(monkeypatch):
    now = datetime.now(UTC)
    conversations = AsyncMock()
    conversations.get_for_agent.return_value = {
        "conversation_id": "conv_1",
        "agent_id": "pa_1",
        "credential_id": "key_1",
        "thread_id": "thread_1",
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    resolver = AsyncMock()
    resolver.resolve.return_value = _context()
    ledger = AsyncMock()
    ledger.reserve.side_effect = QuotaExceededError(
        "daily_runs_exceeded",
        retry_after=321,
    )
    start = AsyncMock()
    monkeypatch.setattr(agent_public_api, "start_run", start)
    app = _router_app(
        resolver=resolver,
        conversations=conversations,
        quota_ledger=ledger,
    )
    response = TestClient(app).post(
        "/api/v1/agents/pa_1/conversations/conv_1/runs",
        json={"message": "hello"},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "321"
    assert response.json()["detail"]["code"] == "daily_runs_exceeded"
    start.assert_not_awaited()
