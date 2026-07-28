from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.agent_channel import (
    SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
    ActiveAgentChannelConflictError,
    AgentChannelRepository,
    AgentChannelRow,
)
from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import PublishedAgentRow


@pytest_asyncio.fixture
async def channel_repo() -> AsyncIterator[AgentChannelRepository]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                PublishedAgentRow(id="pa_1", owner_user_id="owner-a", slug="one", display_name="One", status="published"),
                PublishedAgentRow(id="pa_2", owner_user_id="owner-b", slug="two", display_name="Two", status="published"),
            ]
        )
        await session.commit()
    yield AgentChannelRepository(session_factory)
    await engine.dispose()


def test_agent_channel_model_contains_only_secret_reference() -> None:
    table = Base.metadata.tables[AgentChannelRow.__tablename__]

    assert {
        "id",
        "agent_id",
        "channel_type",
        "app_id",
        "secret_ref",
        "connection_mode",
        "status",
        "delete_previous_status",
        "runtime_lease_token",
        "runtime_lease_expires_at",
        "runtime_generation",
        "secret_cleanup_ref",
        "secret_cleanup_reason",
        "secret_cleanup_not_before",
        "rotation_previous_secret_ref",
        "health",
        "health_detail",
        "health_revision",
        "created_at",
        "updated_at",
        "last_started_at",
    } <= set(table.columns.keys())
    assert not ({"app_secret", "secret", "token", "encrypt_key"} & set(table.columns.keys()))
    assert "uq_agent_channels_active" in {index.name for index in table.indexes}


@pytest.mark.asyncio
async def test_agent_channel_crud_is_owner_scoped(channel_repo: AgentChannelRepository) -> None:
    created = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_a",
        secret_ref="secret://feishu/11111111111111111111111111111111",
    )

    assert created is not None
    assert created["status"] == "inactive"
    assert created["health"] == "unknown"
    assert "app_secret" not in created
    assert await channel_repo.get("pa_1", created["id"], owner_user_id="owner-b") is None
    assert await channel_repo.list_by_agent("pa_1", owner_user_id="owner-b") == []
    assert (
        await channel_repo.update_credentials(
            "pa_1",
            created["id"],
            owner_user_id="owner-b",
            app_id="stolen",
            secret_ref="secret://feishu/22222222222222222222222222222222",
        )
        is None
    )

    updated = await channel_repo.update_credentials(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        app_id="cli_rotated",
        secret_ref="secret://feishu/33333333333333333333333333333333",
    )
    assert updated is not None
    assert updated["app_id"] == "cli_rotated"
    assert updated["runtime_generation"] == created["runtime_generation"] + 1
    assert updated["health_revision"] == 0


@pytest.mark.asyncio
async def test_only_one_active_feishu_binding_per_agent(channel_repo: AgentChannelRepository) -> None:
    first = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_first",
        secret_ref="secret://feishu/11111111111111111111111111111111",
    )
    second = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_second",
        secret_ref="secret://feishu/22222222222222222222222222222222",
    )

    active = await channel_repo.activate("pa_1", first["id"], owner_user_id="owner-a")
    assert active is not None
    assert active["status"] == "active"
    with pytest.raises(ActiveAgentChannelConflictError):
        await channel_repo.activate("pa_1", second["id"], owner_user_id="owner-a")

    stopped = await channel_repo.deactivate("pa_1", first["id"], owner_user_id="owner-a")
    assert stopped is not None
    replacement = await channel_repo.activate("pa_1", second["id"], owner_user_id="owner-a")
    assert replacement is not None
    assert replacement["status"] == "active"


@pytest.mark.asyncio
async def test_health_projection_is_fenced_by_runtime_generation_and_token(channel_repo: AgentChannelRepository) -> None:
    created = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_health_fence",
        secret_ref="secret://feishu/11111111111111111111111111111111",
    )
    active = await channel_repo.activate("pa_1", created["id"], owner_user_id="owner-a")
    assert active is not None
    claimed = await channel_repo.claim_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="current-token",
    )
    assert claimed is not None

    stale_generation = await channel_repo.update_health(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        health="unhealthy",
        detail="stale generation",
        expected_runtime_generation=active["runtime_generation"],
        expected_runtime_lease_token=None,
        health_revision=1,
    )
    stale_token = await channel_repo.update_health(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        health="unhealthy",
        detail="stale token",
        expected_runtime_generation=claimed["runtime_generation"],
        expected_runtime_lease_token="old-token",
        health_revision=2,
    )
    current = await channel_repo.update_health(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        health="healthy",
        expected_runtime_generation=claimed["runtime_generation"],
        expected_runtime_lease_token="current-token",
        health_revision=3,
    )

    assert stale_generation is None
    assert stale_token is None
    assert current is not None
    assert current["health"] == "healthy"
    assert current["health_detail"] is None


@pytest.mark.asyncio
async def test_reconcile_runtime_claim_atomically_clears_only_the_exact_token(
    channel_repo: AgentChannelRepository,
) -> None:
    created = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_reconcile",
        secret_ref="secret://feishu/11111111111111111111111111111111",
    )
    active = await channel_repo.activate("pa_1", created["id"], owner_user_id="owner-a")
    assert active is not None
    claimed = await channel_repo.claim_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="ambiguous-token",
    )
    assert claimed is not None

    foreign = await channel_repo.reconcile_runtime_claim(
        created["id"],
        lease_token="different-token",
        supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
    )
    assert foreign.exact_token_released is False
    still_claimed = await channel_repo.get("pa_1", created["id"], owner_user_id="owner-a")
    assert still_claimed is not None
    assert still_claimed["runtime_lease_token"] == "ambiguous-token"

    exact = await channel_repo.reconcile_runtime_claim(
        created["id"],
        lease_token="ambiguous-token",
        supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
        failure_health="unhealthy",
        failure_detail="Feishu channel failed to start",
        expected_claim_generation=claimed["runtime_generation"],
    )
    assert exact.exact_token_released is True
    assert exact.failure_health_current is True
    reconciled = await channel_repo.get("pa_1", created["id"], owner_user_id="owner-a")
    assert reconciled is not None
    assert reconciled["runtime_lease_token"] is None
    assert reconciled["runtime_generation"] == claimed["runtime_generation"] + 1
    assert reconciled["health"] == "unhealthy"
    assert reconciled["health_detail"] == "Feishu channel failed to start"
    assert reconciled["health_revision"] == 1


@pytest.mark.asyncio
async def test_reconcile_failed_claim_does_not_overwrite_successor_runtime_health(
    channel_repo: AgentChannelRepository,
) -> None:
    created = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_successor",
        secret_ref="secret://feishu/11111111111111111111111111111111",
    )
    active = await channel_repo.activate("pa_1", created["id"], owner_user_id="owner-a")
    assert active is not None
    abandoned = await channel_repo.claim_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="abandoned-token",
    )
    assert abandoned is not None
    released = await channel_repo.release_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="abandoned-token",
        expected_runtime_generation=abandoned["runtime_generation"],
    )
    assert released is not None
    successor = await channel_repo.claim_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="successor-token",
    )
    assert successor is not None
    healthy = await channel_repo.update_health(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        health="healthy",
        expected_runtime_generation=successor["runtime_generation"],
        expected_runtime_lease_token="successor-token",
        health_revision=1,
    )
    assert healthy is not None

    outcome = await channel_repo.reconcile_runtime_claim(
        created["id"],
        lease_token="abandoned-token",
        supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
        failure_health="unhealthy",
        failure_detail="Feishu channel failed to start",
        expected_claim_generation=abandoned["runtime_generation"],
    )

    assert outcome.exact_token_released is False
    assert outcome.failure_health_current is False
    current = await channel_repo.get("pa_1", created["id"], owner_user_id="owner-a")
    assert current is not None
    assert current["runtime_lease_token"] == "successor-token"
    assert current["health"] == "healthy"


@pytest.mark.asyncio
async def test_health_projection_rejects_older_observation_in_same_runtime_generation(channel_repo: AgentChannelRepository) -> None:
    created = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_health_order",
        secret_ref="secret://feishu/33333333333333333333333333333333",
    )
    active = await channel_repo.activate("pa_1", created["id"], owner_user_id="owner-a")
    assert active is not None
    claimed = await channel_repo.claim_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="ordered-runtime",
    )
    assert claimed is not None

    newest = await channel_repo.update_health(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        health="healthy",
        expected_runtime_generation=claimed["runtime_generation"],
        expected_runtime_lease_token="ordered-runtime",
        health_revision=2,
    )
    stale = await channel_repo.update_health(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        health="unhealthy",
        expected_runtime_generation=claimed["runtime_generation"],
        expected_runtime_lease_token="ordered-runtime",
        health_revision=1,
    )
    stored = await channel_repo.get("pa_1", created["id"], owner_user_id="owner-a")

    assert newest is not None
    assert stale is None
    assert stored is not None
    assert stored["health"] == "healthy"
    assert stored["health_revision"] == 2


@pytest.mark.asyncio
async def test_runtime_release_acknowledgement_is_idempotent_for_exact_generation(channel_repo: AgentChannelRepository) -> None:
    created = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_release_ack",
        secret_ref="secret://feishu/44444444444444444444444444444444",
    )
    active = await channel_repo.activate("pa_1", created["id"], owner_user_id="owner-a")
    assert active is not None
    claimed = await channel_repo.claim_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="release-ack-token",
    )
    assert claimed is not None

    released = await channel_repo.release_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="release-ack-token",
        expected_runtime_generation=claimed["runtime_generation"],
    )
    replayed = await channel_repo.release_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="release-ack-token",
        expected_runtime_generation=claimed["runtime_generation"],
    )
    stale = await channel_repo.release_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="release-ack-token",
        expected_runtime_generation=claimed["runtime_generation"] - 1,
    )

    assert released is not None
    assert replayed is not None
    assert replayed["runtime_lease_token"] is None
    assert replayed["runtime_generation"] == released["runtime_generation"]
    assert replayed["status"] == released["status"]
    assert stale is None


@pytest.mark.asyncio
async def test_supervisor_active_scan_requires_explicit_system_scope(channel_repo: AgentChannelRepository) -> None:
    first = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_first",
        secret_ref="secret://feishu/11111111111111111111111111111111",
    )
    other = await channel_repo.create(
        agent_id="pa_2",
        owner_user_id="owner-b",
        app_id="cli_other",
        secret_ref="secret://feishu/22222222222222222222222222222222",
    )
    await channel_repo.activate("pa_1", first["id"], owner_user_id="owner-a")
    await channel_repo.activate("pa_2", other["id"], owner_user_id="owner-b")

    with pytest.raises(PermissionError):
        await channel_repo.list_active(supervisor_scope=object())
    active = await channel_repo.list_active(supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE)

    assert {(row["owner_user_id"], row["agent_id"]) for row in active} == {
        ("owner-a", "pa_1"),
        ("owner-b", "pa_2"),
    }


@pytest.mark.asyncio
async def test_deleting_tombstone_preserves_secret_ref_and_previous_status(channel_repo: AgentChannelRepository) -> None:
    created = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_delete",
        secret_ref="secret://feishu/11111111111111111111111111111111",
    )
    await channel_repo.activate("pa_1", created["id"], owner_user_id="owner-a")

    deleting = await channel_repo.mark_deleting(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
    )

    assert deleting is not None
    assert deleting["status"] == "deleting"
    assert deleting["delete_previous_status"] == "active"
    assert deleting["secret_ref"] == created["secret_ref"]
    assert await channel_repo.list_active(supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE) == []
    pending = await channel_repo.list_deleting(supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE)
    assert [(row["id"], row["owner_user_id"]) for row in pending] == [(created["id"], "owner-a")]
    assert await channel_repo.activate("pa_1", created["id"], owner_user_id="owner-a") is None
    assert await channel_repo.deactivate("pa_1", created["id"], owner_user_id="owner-a") is None
    assert (
        await channel_repo.update_credentials(
            "pa_1",
            created["id"],
            owner_user_id="owner-a",
            app_id="must-not-rotate",
            secret_ref="secret://feishu/22222222222222222222222222222222",
        )
        is None
    )


@pytest.mark.asyncio
async def test_runtime_claim_is_atomically_revoked_by_deletion_tombstone(channel_repo: AgentChannelRepository) -> None:
    created = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_runtime",
        secret_ref="secret://feishu/11111111111111111111111111111111",
    )
    await channel_repo.activate("pa_1", created["id"], owner_user_id="owner-a")
    claimed = await channel_repo.claim_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="runtime-a",
    )
    assert claimed is not None
    assert claimed["runtime_lease_token"] == "runtime-a"
    assert (
        await channel_repo.claim_runtime(
            "pa_1",
            created["id"],
            owner_user_id="owner-a",
            lease_token="runtime-b",
        )
        is None
    )
    confirmed = await channel_repo.confirm_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="runtime-a",
    )
    assert confirmed is not None

    deleting = await channel_repo.mark_deleting("pa_1", created["id"], owner_user_id="owner-a")
    assert deleting is not None
    assert deleting["runtime_lease_token"] == "runtime-a"
    assert not await channel_repo.renew_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="runtime-a",
    )
    assert (
        await channel_repo.claim_runtime(
            "pa_1",
            created["id"],
            owner_user_id="owner-a",
            lease_token="stale-runtime",
        )
        is None
    )
    assert await channel_repo.release_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="runtime-a",
    )


@pytest.mark.asyncio
async def test_expired_runtime_timestamp_does_not_authorize_takeover(channel_repo: AgentChannelRepository) -> None:
    created = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_expired_runtime",
        secret_ref="secret://feishu/33333333333333333333333333333333",
    )
    await channel_repo.activate("pa_1", created["id"], owner_user_id="owner-a")
    assert await channel_repo.claim_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="runtime-a",
        lease_seconds=0.1,
    )
    await asyncio.sleep(0.11)

    assert (
        await channel_repo.claim_runtime(
            "pa_1",
            created["id"],
            owner_user_id="owner-a",
            lease_token="runtime-b",
        )
        is None
    )
    assert await channel_repo.release_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="runtime-a",
    )


@pytest.mark.asyncio
async def test_only_system_leader_recovery_scope_can_clear_orphaned_runtime_claim(
    channel_repo: AgentChannelRepository,
) -> None:
    created = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_crashed_runtime",
        secret_ref="secret://feishu/55555555555555555555555555555555",
    )
    await channel_repo.activate("pa_1", created["id"], owner_user_id="owner-a")
    assert await channel_repo.claim_runtime(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        lease_token="crashed-runtime",
    )

    with pytest.raises(PermissionError):
        await channel_repo.recover_orphaned_runtime_leases(supervisor_scope=object())
    retained = await channel_repo.get("pa_1", created["id"], owner_user_id="owner-a")
    assert retained is not None
    assert retained["runtime_lease_token"] == "crashed-runtime"

    assert (
        await channel_repo.recover_orphaned_runtime_leases(
            supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
        )
        == 1
    )
    recovered = await channel_repo.get("pa_1", created["id"], owner_user_id="owner-a")
    assert recovered is not None
    assert recovered["runtime_lease_token"] is None


@pytest.mark.asyncio
async def test_secret_ingest_writer_lease_fences_janitor_until_ready(channel_repo: AgentChannelRepository) -> None:
    secret_ref = "secret://feishu/44444444444444444444444444444444"
    reserved = await channel_repo.reserve_secret_ingest(
        agent_id="pa_1",
        binding_id="ach_writer",
        owner_user_id="owner-a",
        secret_ref=secret_ref,
        defer_seconds=0,
    )
    assert reserved is not None
    writing = await channel_repo.begin_secret_ingest_write(
        secret_ref,
        agent_id="pa_1",
        binding_id="ach_writer",
        owner_user_id="owner-a",
        writer_token="writer-a",
        lease_seconds=60,
    )
    assert writing is not None
    assert writing["state"] == "writing"
    assert await channel_repo.list_secret_ingests_due(supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE) == []

    ready = await channel_repo.complete_secret_ingest_write(
        secret_ref,
        writer_token="writer-a",
        writer_generation=writing["writer_generation"],
    )
    assert ready is not None
    assert ready["state"] == "ready"
    due = await channel_repo.list_secret_ingests_due(supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE)
    assert [row["secret_ref"] for row in due] == [secret_ref]


@pytest.mark.asyncio
async def test_rotation_secret_cleanup_outbox_requires_matching_ack(channel_repo: AgentChannelRepository) -> None:
    created = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_cleanup",
        secret_ref="secret://feishu/11111111111111111111111111111111",
    )
    cleanup_ref = "secret://feishu/22222222222222222222222222222222"
    staged = await channel_repo.stage_secret_cleanup(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        secret_ref=cleanup_ref,
        defer_seconds=0,
    )
    assert staged is not None
    assert staged["rotation_previous_secret_ref"] == created["secret_ref"]
    due = await channel_repo.list_secret_cleanup_due(supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE)
    assert [(row["id"], row["secret_cleanup_ref"]) for row in due] == [(created["id"], cleanup_ref)]
    assert (
        await channel_repo.clear_secret_cleanup(
            "pa_1",
            created["id"],
            owner_user_id="owner-a",
            secret_ref="secret://feishu/33333333333333333333333333333333",
        )
        is False
    )
    assert await channel_repo.clear_secret_cleanup(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        secret_ref=cleanup_ref,
    )


@pytest.mark.asyncio
async def test_crashed_rotation_candidate_preserves_active_ref_and_erases_superseded_ref(channel_repo: AgentChannelRepository) -> None:
    old_ref = "secret://feishu/11111111111111111111111111111111"
    new_ref = "secret://feishu/22222222222222222222222222222222"
    created = await channel_repo.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli_old",
        secret_ref=old_ref,
    )
    await channel_repo.stage_secret_cleanup(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        secret_ref=new_ref,
        defer_seconds=0,
    )
    await channel_repo.update_credentials(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
        app_id="cli_new",
        secret_ref=new_ref,
    )

    recovered = await channel_repo.recover_staged_secret_cleanup(
        "pa_1",
        created["id"],
        owner_user_id="owner-a",
    )

    assert recovered is not None
    assert recovered["secret_ref"] == new_ref
    assert recovered["secret_cleanup_ref"] == old_ref
    assert recovered["secret_cleanup_reason"] == "rotation_superseded"
    assert recovered["rotation_previous_secret_ref"] is None
