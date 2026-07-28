from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.deps import get_agent_usage_repo, get_external_audit_repo
from app.gateway.routers import published_agents
from deerflow.persistence.agent_channel import AgentChannelRepository
from deerflow.persistence.agent_usage import AgentUsageRepository
from deerflow.persistence.agent_usage.model import AgentQuotaRejectionRow
from deerflow.persistence.base import Base
from deerflow.persistence.connector import ConnectorRepository
from deerflow.persistence.published_agent import PublishedAgentRepository
from deerflow.publishing.context import PublishedAgentContext
from deerflow.publishing.quota import EffectiveQuota, QuotaLedger


def test_quota_rejection_model_is_registered_by_unified_metadata():
    from deerflow.persistence import models

    assert models.AgentQuotaRejectionRow is AgentQuotaRejectionRow
    assert AgentQuotaRejectionRow.__tablename__ in Base.metadata.tables
    assert "AgentQuotaRejectionRow" in models.__all__


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


def _usage(
    run_id: str,
    *,
    owner: str = "owner-1",
    agent: str = "pa_1",
    status: str = "success",
    source: str = "api",
    credential_id: str = "key_1",
    release_id: str = "rel_current",
    latency_ms: int = 123,
    event_latency_ms: int | None = None,
):
    actor = "agent-key:key_1"
    return {
        "owner_user_id": owner,
        "agent_id": agent,
        "source": source,
        "credential_id": credential_id,
        "external_actor_hash": hashlib.sha256(actor.encode()).hexdigest(),
        "conversation_id": "conv_1",
        "run_id": run_id,
        "release_id": release_id,
        "model": "model-1",
        "input_tokens": 10,
        "output_tokens": 15,
        "total_tokens": 25,
        "latency_ms": latency_ms,
        "event_latency_ms": event_latency_ms,
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
        "cost_microusd": 0,
    }
    assert result["days"][0]["statuses"] == {status: 1}


@pytest.mark.asyncio
async def test_daily_aggregate_filters_by_source_and_credential(usage_repo):
    await usage_repo.record_usage(
        _usage("run-api-1", credential_id="key_1"),
        owner_user_id="owner-1",
    )
    await usage_repo.record_usage(
        _usage("run-api-2", credential_id="key_2"),
        owner_user_id="owner-1",
    )
    await usage_repo.record_usage(
        _usage(
            "run-feishu",
            source="feishu",
            credential_id="ach_1",
        ),
        owner_user_id="owner-1",
    )

    api_key_result = await usage_repo.aggregate_daily(
        owner_user_id="owner-1",
        agent_id="pa_1",
        since=datetime.now(UTC) - timedelta(days=1),
        source="api",
        credential_id="key_2",
    )
    feishu_result = await usage_repo.aggregate_daily(
        owner_user_id="owner-1",
        agent_id="pa_1",
        since=datetime.now(UTC) - timedelta(days=1),
        source="feishu",
    )

    assert api_key_result["totals"]["runs"] == 1
    assert feishu_result["totals"]["runs"] == 1


@pytest.mark.asyncio
async def test_operational_aggregate_covers_cost_saturation_feishu_connectors_and_current_release(
    usage_repo,
):
    agents = PublishedAgentRepository(usage_repo._sf)
    channels = AgentChannelRepository(usage_repo._sf)
    connectors = ConnectorRepository(usage_repo._sf)
    agent = await agents.create_agent(
        owner_user_id="owner-1",
        slug="ops-agent",
        display_name="Ops Agent",
    )
    assert await agents.set_current_release(
        agent["id"],
        owner_user_id="owner-1",
        release_id="rel_current",
    )
    binding = await channels.create(
        agent_id=agent["id"],
        owner_user_id="owner-1",
        app_id="cli_ops",
        secret_ref="secret://feishu/ops",
    )
    assert binding is not None
    activated = await channels.activate(
        agent["id"],
        binding["id"],
        owner_user_id="owner-1",
    )
    assert activated is not None
    await channels.update_health(
        agent["id"],
        binding["id"],
        owner_user_id="owner-1",
        health="unhealthy",
        detail="connection unavailable",
        expected_runtime_generation=activated["runtime_generation"],
        expected_runtime_lease_token=activated["runtime_lease_token"],
        health_revision=activated["health_revision"] + 1,
    )

    await usage_repo.record_usage(
        _usage(
            "run-current-success",
            agent=agent["id"],
            source="feishu",
            credential_id=binding["id"],
            latency_ms=100,
            event_latency_ms=80,
        ),
        owner_user_id="owner-1",
    )
    await usage_repo.record_usage(
        _usage(
            "run-current-failed",
            agent=agent["id"],
            source="feishu",
            credential_id=binding["id"],
            status="failed",
            latency_ms=300,
            event_latency_ms=120,
        ),
        owner_user_id="owner-1",
    )
    await usage_repo.record_usage(
        _usage(
            "run-old-release",
            agent=agent["id"],
            release_id="rel_old",
        ),
        owner_user_id="owner-1",
    )

    quota = EffectiveQuota(
        agent_max_concurrent_runs=1,
        agent_daily_runs=10,
        agent_daily_tokens=10_000,
        agent_inbound_rps=100,
        max_concurrent_runs=1,
        daily_runs=10,
        daily_tokens=10_000,
        max_run_seconds=60,
        max_tokens_per_run=100,
        max_input_bytes=10_000,
        inbound_rps=100,
    )
    quota_context = PublishedAgentContext(
        owner_user_id="owner-1",
        agent_id=agent["id"],
        release_id="rel_current",
        source="api",
        credential_id="key_1",
        external_actor="actor",
        conversation_scope="conv_1",
        skill_revision_ids=(),
        connector_capabilities=(),
        tool_groups=(),
        model_name="model-1",
        instructions="instructions",
        effective_quota=quota,
        correlation_id="req_ops",
        idempotency_key=None,
    )
    ledger = QuotaLedger(usage_repo)
    await ledger.reserve(quota_context, request_key="ops-running")
    with pytest.raises(Exception, match="max_concurrent_runs_exceeded"):
        await ledger.reserve(quota_context, request_key="ops-saturated")

    await connectors.append_audit(
        {
            "connector_id": "conn_ops",
            "connector_type": "acceptance",
            "user_id": "owner-1",
            "tenant_id": None,
            "thread_id": "thread-ops",
            "run_id": "run-current-failed",
            "agent_id": agent["id"],
            "skill_name": None,
            "capability": "mail.send",
            "operation": "execute",
            "decision": "error",
            "request_summary_json": {},
            "result_summary_json": {},
            "error_code": "connector.failed",
            "error_message": "safe failure",
            "elapsed_ms": 12,
        }
    )
    await connectors.append_audit(
        {
            "connector_id": "conn_ops",
            "connector_type": "acceptance",
            "user_id": "owner-1",
            "tenant_id": None,
            "thread_id": "thread-ops",
            "run_id": "run-current-failed",
            "agent_id": agent["id"],
            "skill_name": None,
            "capability": "mail.delete",
            "operation": "execute",
            "decision": "deny",
            "request_summary_json": {},
            "result_summary_json": {},
            "error_code": "connector.denied",
            "error_message": "safe denial",
            "elapsed_ms": 1,
        }
    )

    result = await usage_repo.aggregate_daily(
        owner_user_id="owner-1",
        agent_id=agent["id"],
        since=datetime.now(UTC) - timedelta(days=1),
        model_costs={
            "model-1": {
                "input_usd_per_million_tokens": 2,
                "output_usd_per_million_tokens": 4,
            }
        },
    )

    assert result["totals"]["cost_microusd"] == 240
    assert result["operations"] == {
        "agent_status": "published",
        "agent_active": True,
        "active_bindings": 1,
        "unhealthy_bindings": 1,
        "quota_rejections": 1,
        "concurrency_saturation": 1,
        "feishu_event_latency_ms": {"average": 100, "p95": 120},
        "connector_failures": 1,
        "connector_denials": 1,
        "current_release_id": "rel_current",
        "current_release_runs": 2,
        "current_release_errors": 1,
        "current_release_error_rate": 0.5,
    }


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

    captured = {}

    async def aggregate_daily(**kwargs):
        captured.update(kwargs)
        return {"agent_id": kwargs["agent_id"], "days": [], "totals": {"runs": 0}}

    usage.aggregate_daily = aggregate_daily
    app.dependency_overrides[get_agent_usage_repo] = lambda: usage
    client = TestClient(app)
    own = client.get("/api/published-agents/pa_1/usage?source=api&key_id=key_1")
    assert own.status_code == 200
    assert own.json()["agent_id"] == "pa_1"
    assert captured["source"] == "api"
    assert captured["credential_id"] == "key_1"
    other = client.get(
        "/api/published-agents/pa_1/usage",
        headers={"X-Owner": "owner-2"},
    )
    assert other.status_code == 404


def test_owner_rejection_audit_is_metadata_only():
    app = FastAPI()

    @app.middleware("http")
    async def session_auth(request: Request, call_next):
        request.state.user = SimpleNamespace(id="owner-1")
        request.state.auth_method = "session"
        return await call_next(request)

    class _Audit:
        async def list(self, **kwargs):
            assert kwargs == {
                "owner_user_id": "owner-1",
                "agent_id": "pa_1",
                "minimum_status_code": 400,
                "limit": 20,
            }
            return [
                {
                    "id": "audit_1",
                    "request_id": "req_12345678",
                    "source": "api",
                    "credential_id": "key_1",
                    "external_actor_hash": "must-not-leak",
                    "client_ip_hash": "must-not-leak",
                    "user_agent": "must-not-leak",
                    "action": "post:create_agent_run",
                    "resource_type": "agent",
                    "resource_id": "pa_1",
                    "skill_name": None,
                    "method": "POST",
                    "path_template": "/api/v1/agents/{agent_id}/runs",
                    "status_code": 429,
                    "duration_ms": 3,
                    "created_at": "2026-07-24T00:00:00Z",
                }
            ]

    app.include_router(published_agents.router)
    app.dependency_overrides[published_agents.get_draft_service] = _DraftService
    app.dependency_overrides[get_external_audit_repo] = _Audit
    response = TestClient(app).get("/api/published-agents/pa_1/audit")

    assert response.status_code == 200
    body = response.json()[0]
    assert body["category"] == "quota"
    assert "external_actor_hash" not in body
    assert "client_ip_hash" not in body
    assert "user_agent" not in body
