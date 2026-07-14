from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.deps import get_agent_usage_repo
from app.gateway.routers import published_agents
from deerflow.persistence.agent_usage import AgentUsageRepository
from deerflow.persistence.base import Base
from deerflow.publishing.quota import EffectiveQuota, QuotaLedger


@pytest_asyncio.fixture()
async def usage_repo(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repo = AgentUsageRepository(async_sessionmaker(engine, expire_on_commit=False))
    try:
        yield repo
    finally:
        await engine.dispose()


def _usage(run_id: str, *, owner: str = "owner-1", agent: str = "pa_1", status: str = "success"):
    actor = "agent-key:key_1"
    return {
        "owner_user_id": owner,
        "agent_id": agent,
        "source": "api",
        "credential_id": "key_1",
        "external_actor_hash": hashlib.sha256(actor.encode()).hexdigest(),
        "conversation_id": "conv_1",
        "run_id": run_id,
        "model": "model-1",
        "input_tokens": 10,
        "output_tokens": 15,
        "total_tokens": 25,
        "latency_ms": 123,
        "status": status,
        "error_class": None if status == "success" else "RunError",
        "idempotency_key": "idem-1",
        "correlation_id": "req_12345678",
    }


@pytest.mark.asyncio
async def test_usage_is_inserted_once_per_run_and_actor_is_hashed(usage_repo):
    first, created = await usage_repo.record_usage(_usage("run-1"), owner_user_id="owner-1")
    second, created_again = await usage_repo.record_usage(
        {**_usage("run-1"), "total_tokens": 999},
        owner_user_id="owner-1",
    )
    assert created is True
    assert created_again is False
    assert first["id"] == second["id"]
    assert second["total_tokens"] == 25
    assert second["external_actor_hash"] == hashlib.sha256(b"agent-key:key_1").hexdigest()
    assert "agent-key:key_1" not in repr(second)


@pytest.mark.asyncio
async def test_usage_run_conflict_never_returns_another_owners_row(usage_repo):
    await usage_repo.record_usage(_usage("run-shared", owner="owner-1"), owner_user_id="owner-1")

    row, created = await usage_repo.record_usage(
        _usage("run-shared", owner="owner-2"),
        owner_user_id="owner-2",
    )

    assert row is None
    assert created is False


@pytest.mark.asyncio
async def test_usage_insert_and_reservation_settlement_are_one_idempotent_path(usage_repo):
    quota = EffectiveQuota(
        agent_max_concurrent_runs=2,
        agent_daily_runs=10,
        agent_daily_tokens=10_000,
        agent_inbound_rps=100,
        max_concurrent_runs=2,
        daily_runs=10,
        daily_tokens=10_000,
        max_run_seconds=60,
        max_tokens_per_run=100,
        max_input_bytes=10_000,
        inbound_rps=100,
    )
    context = SimpleNamespace(
        owner_user_id="owner-1",
        agent_id="pa_atomic",
        credential_id="key_1",
        effective_quota=quota,
    )
    ledger = QuotaLedger(usage_repo)
    reservation = await ledger.reserve(context, request_key="atomic-request")
    values = _usage("run-atomic", agent="pa_atomic")
    assert await ledger.settle(
        reservation.id,
        owner_user_id="owner-1",
        tokens_used=25,
        status="success",
        run_id="run-atomic",
        usage=values,
    )
    assert not await ledger.settle(
        reservation.id,
        owner_user_id="owner-1",
        tokens_used=999,
        status="success",
        run_id="run-atomic",
        usage={**values, "total_tokens": 999},
    )
    stored, created = await usage_repo.record_usage(
        {**values, "total_tokens": 999},
        owner_user_id="owner-1",
    )
    assert created is False
    assert stored["total_tokens"] == 25


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["success", "failed", "cancelled", "timeout"])
async def test_daily_aggregate_includes_all_terminal_statuses(usage_repo, status):
    await usage_repo.record_usage(
        _usage(f"run-{status}", agent=f"pa-{status}", status=status),
        owner_user_id="owner-1",
    )
    result = await usage_repo.aggregate_daily(
        owner_user_id="owner-1",
        agent_id=f"pa-{status}",
        since=datetime.now(UTC) - timedelta(days=1),
    )
    assert result["totals"] == {
        "runs": 1,
        "input_tokens": 10,
        "output_tokens": 15,
        "total_tokens": 25,
    }
    assert result["days"][0]["statuses"] == {status: 1}


class _DraftService:
    async def get_agent(self, agent_id, *, owner_user_id):
        if (agent_id, owner_user_id) == ("pa_1", "owner-1"):
            return {"id": "pa_1"}
        return None


def test_owner_usage_view_is_tenant_scoped():
    app = FastAPI()

    @app.middleware("http")
    async def session_auth(request: Request, call_next):
        request.state.user = SimpleNamespace(id=request.headers.get("X-Owner", "owner-1"))
        request.state.auth_method = "session"
        return await call_next(request)

    app.include_router(published_agents.router)
    app.dependency_overrides[published_agents.get_draft_service] = _DraftService
    usage = SimpleNamespace(
        aggregate_daily=lambda **kwargs: None,
    )

    async def aggregate_daily(**kwargs):
        return {"agent_id": kwargs["agent_id"], "days": [], "totals": {"runs": 0}}

    usage.aggregate_daily = aggregate_daily
    app.dependency_overrides[get_agent_usage_repo] = lambda: usage
    client = TestClient(app)
    own = client.get("/api/published-agents/pa_1/usage")
    assert own.status_code == 200
    assert own.json()["agent_id"] == "pa_1"
    other = client.get(
        "/api/published-agents/pa_1/usage",
        headers={"X-Owner": "owner-2"},
    )
    assert other.status_code == 404
