from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.api_key_auth import ExternalAPIAuthMiddleware
from app.gateway.deps import get_config
from app.gateway.external.audit import ExternalAuditMiddleware
from app.gateway.external.config import ExternalAPIConfig, set_external_api_config
from app.gateway.external.service import APIKeyService
from app.gateway.routers import external
from deerflow.persistence.api_key import APIKeyRepository
from deerflow.persistence.base import Base
from deerflow.persistence.external_audit import ExternalAuditRepository
from deerflow.persistence.external_conversation import ExternalConversationRepository
from deerflow.persistence.external_idempotency import ExternalIdempotencyRepository
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime import DisconnectMode, RunRecord, RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore


class FakeLocalProvider:
    def __init__(self, user):
        self.user = user

    async def get_user(self, user_id):
        return self.user if str(self.user.id) == user_id else None


class FakeRunManager:
    def __init__(self, record, store):
        self.record = record
        self.store = store

    async def get(self, run_id, *, user_id=None):
        if run_id == self.record.run_id and user_id == str(self.record.metadata["external_user_id"]):
            return self.record
        return None

    async def cancel(self, run_id):
        self.record.status = RunStatus.interrupted
        await self.store.update_status(run_id, RunStatus.interrupted.value)
        return True


@pytest.mark.anyio
async def test_bearer_conversation_run_and_audit_e2e(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'external-e2e.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    keys = APIKeyRepository(session_factory)
    conversations = ExternalConversationRepository(session_factory)
    idempotency = ExternalIdempotencyRepository(session_factory)
    audit = ExternalAuditRepository(session_factory)
    user = SimpleNamespace(id=uuid4())
    config = ExternalAPIConfig(api_key_pepper="p" * 32)
    set_external_api_config(config)
    key = await APIKeyService(keys, config).rotate(user_id=str(user.id))

    app = FastAPI()
    app.state.api_key_repo = keys
    app.state.external_conversation_repo = conversations
    app.state.external_idempotency_repo = idempotency
    app.state.external_audit_repo = audit
    app.state.checkpointer = InMemorySaver()
    app.state.thread_store = MemoryThreadMetaStore(InMemoryStore())
    app.state.run_store = MemoryRunStore()
    app.add_middleware(ExternalAPIAuthMiddleware)
    app.add_middleware(ExternalAuditMiddleware)
    app.include_router(external.router)
    app.dependency_overrides[get_config] = lambda: object()

    record = RunRecord(
        run_id=f"run_{uuid4().hex}",
        thread_id="pending",
        assistant_id="lead_agent",
        status=RunStatus.pending,
        on_disconnect=DisconnectMode.continue_,
        metadata={},
        created_at="2026-06-08T00:00:00Z",
        updated_at="2026-06-08T00:00:00Z",
    )
    app.state.run_manager = FakeRunManager(record, app.state.run_store)

    async def start_run(body, thread_id, request):
        record.thread_id = thread_id
        record.metadata = {**body.metadata, "external_user_id": user.id}
        await app.state.run_store.put(
            record.run_id,
            thread_id=thread_id,
            user_id=str(user.id),
            status=record.status.value,
            metadata=record.metadata,
            created_at=record.created_at,
        )
        return record

    from app.gateway import deps

    monkeypatch.setattr(deps, "get_local_provider", lambda: FakeLocalProvider(user))
    monkeypatch.setattr(external, "start_run", start_run)

    headers = {"Authorization": f"Bearer {key['api_key']}"}
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/external/conversations",
                headers={**headers, "Idempotency-Key": "conversation-1"},
                json={
                    "source": "crm",
                    "external_conversation_id": "crm-1",
                    "metadata": {"external_user_id": "attacker", "customer_id": "customer-1"},
                },
            )
            assert created.status_code == 201
            assert created.json()["request_id"].startswith("req_")
            assert "thread_id" not in created.json()
            conversation_id = created.json()["conversation_id"]
            mapping = await conversations.get(conversation_id, user_id=str(user.id))
            thread = await app.state.thread_store.get(mapping["thread_id"], user_id=None)
            assert thread["metadata"]["external_user_id"] == str(user.id)
            assert thread["metadata"]["client_metadata"]["external_user_id"] == "attacker"

            new_key = await APIKeyService(keys, config).rotate(user_id=str(user.id))
            old_key_response = await client.get(f"/api/v1/external/conversations/{conversation_id}", headers=headers)
            assert old_key_response.status_code == 401
            headers = {"Authorization": f"Bearer {new_key['api_key']}"}
            continued = await client.get(f"/api/v1/external/conversations/{conversation_id}", headers=headers)
            assert continued.status_code == 200

            started = await client.post(
                f"/api/v1/external/conversations/{conversation_id}/runs",
                headers={**headers, "Idempotency-Key": "run-1"},
                json={"message": "Create a report"},
            )
            assert started.status_code == 202
            assert started.json()["conversation_id"] == conversation_id
            assert record.metadata["external_request_id"] == started.json()["request_id"]

            fetched = await client.get(f"/api/v1/external/runs/{record.run_id}", headers=headers)
            assert fetched.status_code == 200
            assert fetched.json()["status"] == "pending"

            cancelled = await client.post(f"/api/v1/external/runs/{record.run_id}/cancel", headers=headers)
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"

        rows = await audit.list(user_id=str(user.id))
        assert len(rows) == 5
        assert {row["resource_type"] for row in rows} == {"conversation", "run"}
        assert all(row["request_id"] for row in rows)
    finally:
        set_external_api_config(None)
        await engine.dispose()
