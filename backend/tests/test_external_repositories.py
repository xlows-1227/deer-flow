import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.api_key import APIKeyRepository
from deerflow.persistence.base import Base
from deerflow.persistence.external_audit import ExternalAuditRepository
from deerflow.persistence.external_conversation import ExternalConversationExistsError, ExternalConversationRepository
from deerflow.persistence.external_idempotency import ExternalIdempotencyRepository, IdempotencyConflictError


@asynccontextmanager
async def _repository_bundle(database_url: str):
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield {
            "keys": APIKeyRepository(sf),
            "conversations": ExternalConversationRepository(sf),
            "idempotency": ExternalIdempotencyRepository(sf),
            "audit": ExternalAuditRepository(sf),
        }
    finally:
        await engine.dispose()


@pytest.fixture
async def repos(tmp_path):
    async with _repository_bundle(f"sqlite+aiosqlite:///{tmp_path / 'external.db'}") as bundle:
        yield bundle


@pytest.fixture
async def memory_repos():
    async with _repository_bundle("sqlite+aiosqlite:///:memory:") as bundle:
        yield bundle


@pytest.mark.anyio
async def test_api_key_rotate_revokes_previous_and_never_stores_plaintext(repos):
    repository = repos["keys"]
    first = await repository.rotate(
        {
            "id": "a" * 32,
            "user_id": "alice",
            "secret_hash": "h" * 64,
            "key_prefix": "dfk_aaaaaaaa",
            "last_four": "last",
            "scopes": ["external:runs:create"],
            "allowed_skills": ["sales-report"],
        }
    )
    second = await repository.rotate(
        {
            "id": "b" * 32,
            "user_id": "alice",
            "secret_hash": "i" * 64,
            "key_prefix": "dfk_bbbbbbbb",
            "last_four": "four",
            "scopes": [],
            "allowed_skills": [],
        }
    )
    assert await repository.get_active_by_id(first["id"]) is None
    assert (await repository.get_active_by_id(second["id"]))["user_id"] == "alice"
    assert "api_key" not in second and "secret" not in second


@pytest.mark.anyio
async def test_api_key_policy_update_preserves_hash_and_enforces_user_scope(repos):
    repository = repos["keys"]
    created = await repository.rotate(
        {
            "id": "c" * 32,
            "user_id": "alice",
            "secret_hash": "h" * 64,
            "key_prefix": "dfk_cccccccc",
            "last_four": "last",
        }
    )
    assert await repository.get_current_for_user("bob") is None
    updated = await repository.update_policy("alice", ["customer-summary"])
    assert updated["secret_hash"] == created["secret_hash"]
    assert updated["allowed_skills"] == ["customer-summary"]


@pytest.mark.anyio
async def test_conversation_mapping_is_user_scoped_and_conflicts(repos):
    repository = repos["conversations"]
    values = {
        "conversation_id": "conv_1",
        "user_id": "alice",
        "source": "crm",
        "external_conversation_id": "crm-1",
        "thread_id": "thread_1",
        "agent_id": "lead_agent",
    }
    await repository.create(values)
    assert (await repository.get("conv_1", user_id="alice"))["thread_id"] == "thread_1"
    assert await repository.get("conv_1", user_id="bob") is None
    with pytest.raises(ExternalConversationExistsError) as exc:
        await repository.create({**values, "conversation_id": "conv_2", "thread_id": "thread_2"})
    assert exc.value.conversation_id == "conv_1"


@pytest.mark.anyio
async def test_published_conversation_lookup_is_owner_scoped(repos):
    repository = repos["conversations"]
    await repository.create(
        {
            "conversation_id": "conv-agent",
            "user_id": "alice",
            "credential_id": "agent-key-1",
            "source": "agent-api:agent-key-1",
            "thread_id": "thread-agent",
            "agent_id": "pa_1",
        }
    )

    assert (
        await repository.get_for_agent(
            "conv-agent",
            owner_user_id="bob",
            agent_id="pa_1",
            credential_id="agent-key-1",
        )
        is None
    )


@pytest.mark.anyio
async def test_idempotency_replay_conflict_and_expiry(repos):
    repository = repos["idempotency"]
    await repository.put(
        {
            "user_id": "alice",
            "api_key_id": "key-1",
            "idempotency_key": "request-1",
            "request_hash": "a" * 64,
            "response_status": 201,
            "response_json": {"conversation_id": "conv_1"},
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        }
    )
    replay = await repository.get(api_key_id="key-1", idempotency_key="request-1", request_hash="a" * 64)
    assert replay["response_json"]["conversation_id"] == "conv_1"
    with pytest.raises(IdempotencyConflictError):
        await repository.get(api_key_id="key-1", idempotency_key="request-1", request_hash="b" * 64)


@pytest.mark.anyio
async def test_idempotency_claim_is_single_owner_and_can_complete(repos):
    repository = repos["idempotency"]
    values = {
        "user_id": "alice",
        "api_key_id": "key-2",
        "idempotency_key": "request-2",
        "request_hash": "c" * 64,
        "run_id": "run-preallocated",
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    first, first_claimed = await repository.claim(values)
    second, second_claimed = await repository.claim(values)
    assert first_claimed is True
    assert second_claimed is False
    assert first["id"] == second["id"]
    assert first["run_id"] == second["run_id"] == "run-preallocated"

    await repository.complete(
        api_key_id="key-2",
        idempotency_key="request-2",
        run_id="run-2",
        response_status=202,
        response_json={"run_id": "run-2"},
    )
    replay = await repository.get(api_key_id="key-2", idempotency_key="request-2", request_hash="c" * 64)
    assert replay["response_json"] == {"run_id": "run-2"}


@pytest.mark.anyio
async def test_idempotency_claim_persists_preallocated_run_id(memory_repos):
    repository = memory_repos["idempotency"]
    values = {
        "user_id": "alice",
        "api_key_id": "key-preallocated",
        "idempotency_key": "request-preallocated",
        "request_hash": "p" * 64,
        "run_id": "run-preallocated",
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }

    first, first_claimed = await repository.claim(values)
    second, second_claimed = await repository.claim(values)

    assert first_claimed is True
    assert second_claimed is False
    assert first["run_id"] == second["run_id"] == "run-preallocated"


@pytest.mark.anyio
async def test_incomplete_idempotency_claim_release_by_run_is_owner_scoped(memory_repos):
    repository = memory_repos["idempotency"]
    values = {
        "user_id": "alice",
        "api_key_id": "key-recovery",
        "idempotency_key": "request-recovery",
        "request_hash": "r" * 64,
        "run_id": "run-never-persisted",
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    await repository.claim(values)

    assert not await repository.release_incomplete_by_run_id(
        run_id="run-never-persisted",
        user_id="bob",
    )
    assert await repository.release_incomplete_by_run_id(
        run_id="run-never-persisted",
        user_id="alice",
    )
    assert (
        await repository.get(
            api_key_id="key-recovery",
            idempotency_key="request-recovery",
            request_hash="r" * 64,
        )
        is None
    )


@pytest.mark.anyio
async def test_concurrent_idempotency_claim_has_one_owner(repos):
    repository = repos["idempotency"]
    values = {
        "user_id": "alice",
        "api_key_id": "key-concurrent",
        "idempotency_key": "request-concurrent",
        "request_hash": "f" * 64,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }

    first, second = await asyncio.gather(repository.claim(values), repository.claim(values))

    assert sorted((first[1], second[1])) == [False, True]
    assert first[0]["id"] == second[0]["id"]


@pytest.mark.anyio
async def test_expired_idempotency_record_can_be_claimed_again(repos):
    repository = repos["idempotency"]
    await repository.put(
        {
            "user_id": "alice",
            "api_key_id": "key-3",
            "idempotency_key": "request-3",
            "request_hash": "d" * 64,
            "response_status": 201,
            "response_json": {"conversation_id": "expired"},
            "expires_at": datetime.now(UTC) - timedelta(seconds=1),
        }
    )

    claimed, is_owner = await repository.claim(
        {
            "user_id": "alice",
            "api_key_id": "key-3",
            "idempotency_key": "request-3",
            "request_hash": "e" * 64,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        }
    )

    assert is_owner is True
    assert claimed["request_hash"] == "e" * 64
    assert claimed["response_json"] is None


@pytest.mark.anyio
async def test_audit_lists_by_user_and_key_without_bodies(repos):
    repository = repos["audit"]
    await repository.append(
        {
            "request_id": "req_1",
            "user_id": "alice",
            "api_key_id": "key-1",
            "action": "run.create",
            "method": "POST",
            "path_template": "/api/v1/external/conversations/{conversation_id}/runs",
            "status_code": 202,
            "duration_ms": 5,
        }
    )
    rows = await repository.list(user_id="alice", api_key_id="key-1")
    assert len(rows) == 1
    assert "request_body" not in rows[0] and "response_body" not in rows[0]

    with pytest.raises(ValueError, match="scope"):
        await repository.list()


@pytest.mark.anyio
async def test_published_audit_agent_query_requires_and_filters_owner(memory_repos):
    repository = memory_repos["audit"]
    for owner in ("owner-a", "owner-b"):
        await repository.append(
            {
                "request_id": f"req-{owner}",
                "owner_user_id": owner,
                "agent_id": "pa_shared",
                "credential_id": f"key-{owner}",
                "action": "run.create",
                "method": "POST",
                "path_template": "/api/v1/agents/{agent_id}/conversations/{conversation_id}/runs",
                "status_code": 202,
                "duration_ms": 5,
            }
        )

    rows = await repository.list(owner_user_id="owner-a", agent_id="pa_shared")

    assert [row["owner_user_id"] for row in rows] == ["owner-a"]
    with pytest.raises(ValueError, match="owner"):
        await repository.list(agent_id="pa_shared")


@pytest.mark.anyio
async def test_published_audit_can_filter_rejections_before_limit(memory_repos):
    repository = memory_repos["audit"]
    for index, status_code in enumerate((202, 429, 403)):
        await repository.append(
            {
                "request_id": f"req-status-{index}",
                "owner_user_id": "owner-a",
                "agent_id": "pa_1",
                "credential_id": "key-1",
                "source": "api",
                "action": "post:create_agent_run",
                "method": "POST",
                "path_template": "/api/v1/agents/{agent_id}/runs",
                "status_code": status_code,
                "duration_ms": 5,
            }
        )

    rows = await repository.list(
        owner_user_id="owner-a",
        agent_id="pa_1",
        minimum_status_code=400,
        limit=1,
    )

    assert len(rows) == 1
    assert rows[0]["status_code"] in {403, 429}
