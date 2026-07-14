from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.routers import agent_public_api
from deerflow.persistence.agent_usage import AgentUsageRepository
from deerflow.persistence.base import Base
from deerflow.publishing.context import PublishedAgentContext
from deerflow.publishing.quota import (
    EffectiveQuota,
    QuotaExceededError,
    QuotaLedger,
    Reservation,
)
from deerflow.runtime import RunStatus


@pytest_asyncio.fixture()
async def quota_repo(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quota.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repo = AgentUsageRepository(async_sessionmaker(engine, expire_on_commit=False))
    try:
        yield repo
    finally:
        await engine.dispose()


def _context(
    *,
    agent_id: str = "pa_1",
    correlation_id: str = "req_1",
    credential_id: str = "key_1",
    max_concurrent_runs: int = 2,
    daily_runs: int = 10,
    agent_max_concurrent_runs: int | None = None,
    agent_daily_runs: int | None = None,
) -> PublishedAgentContext:
    quota = EffectiveQuota(
        agent_max_concurrent_runs=agent_max_concurrent_runs or max_concurrent_runs,
        agent_daily_runs=agent_daily_runs or daily_runs,
        agent_daily_tokens=10_000,
        agent_inbound_rps=100,
        max_concurrent_runs=max_concurrent_runs,
        daily_runs=daily_runs,
        daily_tokens=10_000,
        max_run_seconds=60,
        max_tokens_per_run=100,
        max_input_bytes=10_000,
        inbound_rps=100,
    )
    return PublishedAgentContext(
        owner_user_id="owner-1",
        agent_id=agent_id,
        release_id="rel_1",
        source="api",
        credential_id=credential_id,
        external_actor="actor",
        conversation_scope="conv_1",
        skill_revision_ids=(),
        connector_capabilities=(),
        tool_groups=(),
        model_name="model",
        instructions="instructions",
        effective_quota=quota,
        correlation_id=correlation_id,
        idempotency_key=None,
    )


@pytest.mark.asyncio
async def test_concurrent_reservations_never_exceed_agent_limit(quota_repo):
    ledger = QuotaLedger(quota_repo)

    async def reserve(index: int):
        try:
            return await ledger.reserve(
                _context(correlation_id=f"req_{index}"),
                request_key=f"request-{index}",
            )
        except QuotaExceededError as exc:
            return exc

    results = await asyncio.gather(*(reserve(index) for index in range(6)))
    reservations = [item for item in results if isinstance(item, Reservation)]
    rejected = [item for item in results if isinstance(item, QuotaExceededError)]
    assert len(reservations) == 2
    assert len(rejected) == 4
    assert {item.code for item in rejected} == {"max_concurrent_runs_exceeded"}
    rows = await quota_repo.list_reservations(owner_user_id="owner-1", agent_id="pa_1")
    assert sum(row["status"] == "pending" for row in rows) == 2


@pytest.mark.asyncio
async def test_daily_limit_rejects_without_creating_reservation(quota_repo):
    ledger = QuotaLedger(quota_repo)
    first = await ledger.reserve(
        _context(agent_id="pa_daily", daily_runs=1),
        request_key="daily-1",
    )
    await ledger.settle(first.id, owner_user_id="owner-1", tokens_used=10, status="success", run_id="run-daily-1")
    with pytest.raises(QuotaExceededError) as captured:
        await ledger.reserve(
            _context(agent_id="pa_daily", daily_runs=1, correlation_id="req_2"),
            request_key="daily-2",
        )
    assert captured.value.code == "daily_runs_exceeded"
    assert captured.value.retry_after > 0
    assert len(await quota_repo.list_reservations(owner_user_id="owner-1", agent_id="pa_daily")) == 1


@pytest.mark.asyncio
async def test_key_daily_limits_are_isolated_but_agent_limit_still_caps_all_keys(quota_repo):
    ledger = QuotaLedger(quota_repo)
    first = await ledger.reserve(
        _context(
            agent_id="pa_keys",
            credential_id="key_1",
            daily_runs=1,
            agent_daily_runs=2,
        ),
        request_key="key-1-first",
    )
    await ledger.settle(first.id, owner_user_id="owner-1", tokens_used=1, status="success", run_id="run-key-1")

    second = await ledger.reserve(
        _context(
            agent_id="pa_keys",
            credential_id="key_2",
            daily_runs=1,
            agent_daily_runs=2,
        ),
        request_key="key-2-first",
    )
    await ledger.settle(second.id, owner_user_id="owner-1", tokens_used=1, status="success", run_id="run-key-2")

    with pytest.raises(QuotaExceededError) as credential_limit:
        await ledger.reserve(
            _context(
                agent_id="pa_keys",
                credential_id="key_1",
                daily_runs=1,
                agent_daily_runs=3,
            ),
            request_key="key-1-second",
        )
    assert credential_limit.value.code == "daily_runs_exceeded"

    with pytest.raises(QuotaExceededError) as agent_limit:
        await ledger.reserve(
            _context(
                agent_id="pa_keys",
                credential_id="key_3",
                daily_runs=1,
                agent_daily_runs=2,
            ),
            request_key="key-3-first",
        )
    assert agent_limit.value.code == "daily_runs_exceeded"


@pytest.mark.asyncio
async def test_request_key_reservation_is_idempotent(quota_repo):
    ledger = QuotaLedger(quota_repo)
    context = _context(agent_id="pa_idem")
    first = await ledger.reserve(context, request_key="same-key")
    second = await ledger.reserve(context, request_key="same-key")
    assert first.id == second.id
    assert len(await quota_repo.list_reservations(owner_user_id="owner-1", agent_id="pa_idem")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["success", "cancelled", "timeout", "failed"])
async def test_all_terminal_states_settle_exactly_once(quota_repo, terminal):
    ledger = QuotaLedger(quota_repo)
    context = _context(agent_id=f"pa_{terminal}")
    reservation = await ledger.reserve(context, request_key=f"request-{terminal}")
    assert await ledger.settle(
        reservation.id,
        owner_user_id="owner-1",
        tokens_used=25,
        status=terminal,
        run_id=f"run-{terminal}",
    )
    assert not await ledger.settle(
        reservation.id,
        owner_user_id="owner-1",
        tokens_used=999,
        status=terminal,
        run_id=f"run-{terminal}",
    )
    row = await quota_repo.get_reservation(reservation.id, owner_user_id="owner-1")
    assert row["status"] == "settled"
    assert row["terminal_status"] == terminal
    assert row["tokens_used"] == 25
    assert not any(item["status"] == "pending" for item in await quota_repo.list_reservations(owner_user_id="owner-1", agent_id=f"pa_{terminal}"))


@pytest.mark.asyncio
async def test_release_is_idempotent_and_allows_retry_with_same_request_key(quota_repo):
    ledger = QuotaLedger(quota_repo)
    context = _context(agent_id="pa_release")
    first = await ledger.reserve(context, request_key="retry-key")
    assert await ledger.release(first.id, owner_user_id="owner-1")
    assert not await ledger.release(first.id, owner_user_id="owner-1")
    retried = await ledger.reserve(context, request_key="retry-key")
    assert retried.id != first.id


@pytest.mark.asyncio
async def test_reservation_mutations_and_reads_are_owner_scoped(quota_repo):
    ledger = QuotaLedger(quota_repo)
    context = _context(agent_id="pa-owner-scope")
    reservation = await ledger.reserve(context, request_key="owner-scope")

    assert await quota_repo.get_reservation(reservation.id, owner_user_id="other-owner") is None
    assert await quota_repo.list_reservations(owner_user_id="other-owner", agent_id=context.agent_id) == []
    assert not await ledger.release(reservation.id, owner_user_id="other-owner")
    assert not await ledger.settle(
        reservation.id,
        owner_user_id="other-owner",
        tokens_used=5,
        status="success",
        run_id="run-owner-scope",
    )
    assert await quota_repo.get_reservation(reservation.id, owner_user_id="owner-1") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("run_status", "expected"),
    [
        (RunStatus.success, "success"),
        (RunStatus.error, "failed"),
        (RunStatus.interrupted, "cancelled"),
        (RunStatus.timeout, "timeout"),
    ],
)
async def test_run_completion_callback_maps_every_terminal_state(run_status, expected):
    async def complete():
        if run_status == RunStatus.interrupted:
            raise asyncio.CancelledError

    run_task = asyncio.create_task(complete())
    record = SimpleNamespace(
        task=run_task,
        run_id=f"run-{expected}",
        status=run_status,
        total_tokens=17,
    )
    ledger = AsyncMock()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(run_manager=AsyncMock())))
    context = _context(agent_id=f"pa-callback-{expected}")
    reservation = Reservation(
        id=f"qres-{expected}",
        request_key=f"request-{expected}",
        agent_id=context.agent_id,
        credential_id=context.credential_id,
        reserved_tokens=100,
        status="pending",
    )
    agent_public_api._schedule_quota_settlement(
        request=request,
        record=record,
        reservation=reservation,
        context=context,
        ledger=ledger,
    )
    tasks = list(request.app.state.agent_quota_tasks)
    await asyncio.gather(*tasks)
    ledger.settle.assert_awaited_once()
    call = ledger.settle.await_args
    assert call.args == (reservation.id,)
    assert call.kwargs["tokens_used"] == 17
    assert call.kwargs["status"] == expected
    assert call.kwargs["run_id"] == record.run_id
    usage = call.kwargs["usage"]
    assert usage["agent_id"] == context.agent_id
    assert usage["external_actor_hash"] != context.external_actor
    assert context.external_actor not in usage.values()
    assert usage["status"] == expected


@pytest.mark.asyncio
async def test_timeout_waits_for_worker_token_flush_before_settlement(monkeypatch):
    context = _context(agent_id="pa-timeout-flush")
    context = replace(
        context,
        effective_quota=replace(context.effective_quota, max_run_seconds=1),
    )
    record = SimpleNamespace(
        task=None,
        run_id="run-timeout-flush",
        status=RunStatus.running,
        total_input_tokens=0,
        total_output_tokens=0,
        total_tokens=1,
    )

    async def worker():
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            record.total_input_tokens = 8
            record.total_output_tokens = 15
            record.total_tokens = 23

    record.task = asyncio.create_task(worker())

    class Manager:
        async def cancel(self, run_id):
            assert run_id == record.run_id
            record.status = RunStatus.interrupted
            record.task.cancel()
            return True

    async def timeout_immediately(awaitable, *, timeout):
        assert timeout == 1
        del awaitable
        raise TimeoutError

    monkeypatch.setattr(agent_public_api.asyncio, "wait_for", timeout_immediately)
    ledger = AsyncMock()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(run_manager=Manager())))
    reservation = Reservation(
        id="qres-timeout-flush",
        request_key="request-timeout-flush",
        agent_id=context.agent_id,
        credential_id=context.credential_id,
        reserved_tokens=100,
        status="pending",
    )

    agent_public_api._schedule_quota_settlement(
        request=request,
        record=record,
        reservation=reservation,
        context=context,
        ledger=ledger,
    )
    await asyncio.gather(*request.app.state.agent_quota_tasks)

    assert ledger.settle.await_args.kwargs["tokens_used"] == 23
