import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.api_key import APIKeyRepository
from deerflow.persistence.base import Base
from deerflow.persistence.external_audit import ExternalAuditRepository
from deerflow.persistence.external_conversation import ExternalConversationExistsError, ExternalConversationRepository
from deerflow.persistence.external_idempotency import ExternalIdempotencyRepository, IdempotencyConflictError


@pytest.fixture
async def repos(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'external.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield {
        "keys": APIKeyRepository(sf),
        "conversations": ExternalConversationRepository(sf),
        "idempotency": ExternalIdempotencyRepository(sf),
        "audit": ExternalAuditRepository(sf),
    }
    await engine.dispose()


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
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    first, first_claimed = await repository.claim(values)
    second, second_claimed = await repository.claim(values)
    assert first_claimed is True
    assert second_claimed is False
    assert first["id"] == second["id"]

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
