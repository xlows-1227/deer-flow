from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from support.feishu_shutdown import (
    CleanupRetryBarrier,
    finish_supervisor_cleanup,
    raise_test_cleanup_errors,
    wait_for_supervisor_ownership,
)
from support.feishu_shutdown import (
    wait_for_runtime_token_clear as _wait_for_runtime_token_clear,
)

from app.channels.base import Channel
from app.channels.message_bus import MessageBus, OutboundMessage
from app.channels.supervisor import BindingCleanupPendingError, BindingHealth, BindingNotFoundError, BindingStartError, FeishuSupervisor
from deerflow.config.paths import Paths, get_paths
from deerflow.persistence.agent_channel import SYSTEM_CHANNEL_SUPERVISOR_SCOPE, AgentChannelRepository
from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import PublishedAgentRow
from deerflow.publishing.feishu_credentials import FeishuCredentials, encode_feishu_credentials
from deerflow.publishing.secret_store import LocalEncryptedSecretStore
from deerflow.sandbox.sandbox_provider import SandboxAcquisition


class _FakeFeishuChannel(Channel):
    def __init__(
        self,
        bus: MessageBus,
        *,
        app_id: str,
        app_secret: str,
        verification_token: str,
        encrypt_key: str,
        binding_id: str,
        agent_id: str,
        runtime_error_callback: Any,
        runtime_health_callback: Any | None = None,
        fail_start: bool = False,
        fail_stop: bool = False,
        fail_stop_running: bool = False,
        attachment_cleanup_healthy: bool = True,
    ) -> None:
        super().__init__(name=f"feishu:{binding_id}", bus=bus, config={})
        self.app_id = app_id
        self.app_secret = app_secret
        self.verification_token = verification_token
        self.encrypt_key = encrypt_key
        self.binding_id = binding_id
        self.agent_id = agent_id
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.fail_stop_running = fail_stop_running
        self.runtime_error_callback = runtime_error_callback
        self.runtime_health_callback = runtime_health_callback
        self.attachment_cleanup_healthy = attachment_cleanup_healthy
        self.stop_count = 0

    async def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("connection refused")
        self._running = True

    async def stop(self) -> None:
        self.stop_count += 1
        if self.fail_stop:
            # Match the production FeishuChannel failure shape: observable
            # running state is cleared before session/thread shutdown fails.
            if not self.fail_stop_running:
                self._running = False
            raise RuntimeError("connection did not close")
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        return None


@dataclass
class _Factory:
    fail_app_ids: set[str]
    fail_stop_app_ids: set[str] = field(default_factory=set)
    fail_stop_running_app_ids: set[str] = field(default_factory=set)
    cleanup_unhealthy_app_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.instances: list[_FakeFeishuChannel] = []

    def __call__(
        self,
        bus: MessageBus,
        *,
        app_id: str,
        app_secret: str,
        verification_token: str,
        encrypt_key: str,
        binding_id: str,
        agent_id: str,
        runtime_error_callback: Any,
        runtime_health_callback: Any | None = None,
    ) -> _FakeFeishuChannel:
        channel = _FakeFeishuChannel(
            bus,
            app_id=app_id,
            app_secret=app_secret,
            verification_token=verification_token,
            encrypt_key=encrypt_key,
            binding_id=binding_id,
            agent_id=agent_id,
            runtime_error_callback=runtime_error_callback,
            runtime_health_callback=runtime_health_callback,
            fail_start=app_id in self.fail_app_ids,
            fail_stop=app_id in self.fail_stop_app_ids,
            fail_stop_running=app_id in self.fail_stop_running_app_ids,
            attachment_cleanup_healthy=app_id not in self.cleanup_unhealthy_app_ids,
        )
        self.instances.append(channel)
        return channel


class _TestRuntimeLeaderFence:
    def __init__(self) -> None:
        self.held = False

    async def acquire(self) -> bool:
        self.held = True
        return True

    async def release(self) -> None:
        self.held = False


@pytest.mark.asyncio
async def test_shutdown_test_cleanup_preserves_fence_when_ownership_does_not_converge() -> None:
    class NeverConvergingSupervisor:
        _shutdown_complete = False
        owned_binding_ids = ("binding-still-owned",)

        def __init__(self) -> None:
            self.shutdown_calls = 0

        async def shutdown(self) -> None:
            self.shutdown_calls += 1

    supervisor = NeverConvergingSupervisor()
    fence = _TestRuntimeLeaderFence()
    assert await fence.acquire()

    with pytest.raises(
        AssertionError,
        match="Supervisor ownership did not converge during test cleanup",
    ):
        await finish_supervisor_cleanup(
            supervisor,
            fence=fence,
            attempts=1,
            interval=0,
        )

    assert supervisor.shutdown_calls == 0
    assert fence.held is True


def test_shutdown_test_cleanup_preserves_body_and_cleanup_errors() -> None:
    body_error = AssertionError("regression failed")
    cleanup_error = RuntimeError("cleanup failed")

    with pytest.raises(BaseExceptionGroup) as captured:
        raise_test_cleanup_errors(
            body_error,
            [cleanup_error],
            message="combined shutdown test failure",
        )

    assert captured.value.exceptions == (body_error, cleanup_error)


SupervisorEnv = tuple[AgentChannelRepository, LocalEncryptedSecretStore, dict[str, Any], dict[str, Any]]


@pytest.fixture(scope="module", autouse=True)
def stop_process_scanner_after_supervisor_module() -> Iterator[None]:
    """Do not leave the process-owned scanner to interpreter finalization."""
    yield
    from app.channels.feishu import stop_published_attachment_backlog_scanner

    stop_published_attachment_backlog_scanner()


@pytest_asyncio.fixture
async def supervisor_env(tmp_path: Path) -> AsyncIterator[SupervisorEnv]:
    # Provisional lease renewal deliberately overlaps a slow channel start.
    # A file-backed database gives those independent sessions the same schema,
    # matching production connection semantics on SQLite.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'supervisor.db'}")
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
    repository = AgentChannelRepository(session_factory)
    secrets = LocalEncryptedSecretStore(tmp_path / "secrets", key=Fernet.generate_key())
    refs = [
        await secrets.put(
            encode_feishu_credentials(
                FeishuCredentials(
                    app_secret="secret-one",
                    verification_token="token-one",
                    encrypt_key="encrypt-one",
                )
            )
        ),
        await secrets.put(
            encode_feishu_credentials(
                FeishuCredentials(
                    app_secret="secret-two",
                    verification_token="token-two",
                    encrypt_key="encrypt-two",
                )
            )
        ),
    ]
    first = await repository.create(agent_id="pa_1", owner_user_id="owner-a", app_id="cli_one", secret_ref=refs[0])
    second = await repository.create(agent_id="pa_2", owner_user_id="owner-b", app_id="cli_two", secret_ref=refs[1])
    yield repository, secrets, first, second
    await engine.dispose()


async def _prepare_rotation_secret(
    repository: AgentChannelRepository,
    secrets: LocalEncryptedSecretStore,
    binding: dict[str, Any],
    *,
    owner_user_id: str,
    app_secret: str = "rotated-secret",
) -> str:
    """Create one ready credential ingest through the production repository seam."""
    secret_ref = secrets.new_ref()
    reserved = await repository.reserve_secret_ingest(
        agent_id=str(binding["agent_id"]),
        binding_id=str(binding["id"]),
        owner_user_id=owner_user_id,
        secret_ref=secret_ref,
    )
    assert reserved is not None
    writing = await repository.begin_secret_ingest_write(
        secret_ref,
        agent_id=str(binding["agent_id"]),
        binding_id=str(binding["id"]),
        owner_user_id=owner_user_id,
        writer_token="rotation-test-writer",
    )
    assert writing is not None
    await secrets.put_reserved(
        secret_ref,
        encode_feishu_credentials(
            FeishuCredentials(
                app_secret=app_secret,
                verification_token="rotated-token",
                encrypt_key="rotated-key",
            )
        ),
    )
    assert await repository.complete_secret_ingest_write(
        secret_ref,
        writer_token="rotation-test-writer",
        writer_generation=writing["writer_generation"],
    )
    return secret_ref


@pytest.mark.asyncio
async def test_starting_and_stopping_one_binding_does_not_affect_another(supervisor_env: SupervisorEnv) -> None:
    repository, secrets, first, second = supervisor_env
    factory = _Factory(set())
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)

    await supervisor.start_binding(first["id"])
    await supervisor.start_binding(second["id"])

    assert set(supervisor.running_binding_ids) == {first["id"], second["id"]}
    assert all(item.health == "healthy" for item in supervisor.health().values())
    await supervisor.stop_binding(first["id"])
    assert set(supervisor.running_binding_ids) == {second["id"]}
    assert factory.instances[0].stop_count == 1
    assert factory.instances[1].is_running is True
    assert (await repository.get("pa_1", first["id"], owner_user_id="owner-a"))["status"] == "inactive"
    assert (await repository.get("pa_2", second["id"], owner_user_id="owner-b"))["status"] == "active"


@pytest.mark.asyncio
async def test_blocked_binding_start_does_not_serialize_peer_lifecycle(supervisor_env: SupervisorEnv) -> None:
    repository, secrets, first, second = supervisor_env
    first_start_entered = asyncio.Event()
    release_first_start = asyncio.Event()
    factory = _Factory(set())

    def blocking_factory(*args: Any, **kwargs: Any) -> _FakeFeishuChannel:
        channel = factory(*args, **kwargs)
        original_start = channel.start

        async def start() -> None:
            if channel.app_id == "cli_one":
                first_start_entered.set()
                await release_first_start.wait()
            await original_start()

        channel.start = start
        return channel

    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=blocking_factory,
    )

    blocked_start = asyncio.create_task(supervisor.start_binding(first["id"]))
    await asyncio.wait_for(first_start_entered.wait(), timeout=1.0)
    try:
        await asyncio.wait_for(supervisor.start_binding(second["id"]), timeout=0.2)
        await asyncio.wait_for(supervisor.restart_binding(second["id"]), timeout=0.2)
        await asyncio.wait_for(supervisor.stop_binding(second["id"]), timeout=0.2)
    finally:
        release_first_start.set()
        await blocked_start

    assert supervisor.running_binding_ids == (first["id"],)


@pytest.mark.asyncio
async def test_loading_active_bindings_isolates_start_failures(supervisor_env: SupervisorEnv) -> None:
    repository, secrets, first, second = supervisor_env
    await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    await repository.activate("pa_2", second["id"], owner_user_id="owner-b")
    factory = _Factory({"cli_one"})
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)

    await supervisor.load_active_bindings()

    assert set(supervisor.running_binding_ids) == {second["id"]}
    assert supervisor.health()[first["id"]].health == "unhealthy"
    assert supervisor.health()[second["id"]].health == "healthy"
    assert (await repository.get("pa_1", first["id"], owner_user_id="owner-a"))["health"] == "unhealthy"
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_loading_active_bindings_isolates_runtime_confirmation_failure(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, second = supervisor_env
    await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    await repository.activate("pa_2", second["id"], owner_user_id="owner-b")
    original_confirm = repository.confirm_runtime

    async def confirm_runtime(agent_id: str, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        if agent_id == "pa_1":
            return None
        return await original_confirm(agent_id, *args, **kwargs)

    monkeypatch.setattr(repository, "confirm_runtime", confirm_runtime)
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=_Factory(set()),
        runtime_leader_fence=_TestRuntimeLeaderFence(),
    )

    await supervisor.load_active_bindings()

    assert supervisor.running_binding_ids == (second["id"],)
    assert supervisor.health()[first["id"]].health == "unhealthy"
    assert supervisor.health()[first["id"]].running is False
    assert supervisor.health()[second["id"]].health == "healthy"
    assert supervisor._cleanup_janitor_task is not None
    failed = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert failed is not None
    assert failed["runtime_lease_token"] is None
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_loading_active_bindings_isolates_initial_repository_read_failure(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, second = supervisor_env
    await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    await repository.activate("pa_2", second["id"], owner_user_id="owner-b")
    original_get = repository.get_for_supervisor
    failed_once = False

    async def get_for_supervisor(binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        nonlocal failed_once
        if binding_id == first["id"] and not failed_once:
            failed_once = True
            raise RuntimeError("one row read failed")
        return await original_get(binding_id, **kwargs)

    monkeypatch.setattr(repository, "get_for_supervisor", get_for_supervisor)
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=_Factory(set()),
        runtime_leader_fence=_TestRuntimeLeaderFence(),
    )

    await supervisor.load_active_bindings()

    assert supervisor.running_binding_ids == (second["id"],)
    assert supervisor.health()[first["id"]].health == "unhealthy"
    assert supervisor.health()[second["id"]].health == "healthy"
    assert supervisor._cleanup_janitor_task is not None
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_slow_ready_start_uses_provisional_lease_budget(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_LEASE_TTL_SECONDS", 1.0)
    original_renew = repository.renew_runtime
    lease_renewed = asyncio.Event()

    async def observe_renewal(*args: Any, **kwargs: Any) -> bool:
        renewed = await original_renew(*args, **kwargs)
        if renewed:
            lease_renewed.set()
        return renewed

    monkeypatch.setattr(repository, "renew_runtime", observe_renewal)
    factory = _Factory(set())

    def slow_factory(*args: Any, **kwargs: Any) -> _FakeFeishuChannel:
        channel = factory(*args, **kwargs)
        original_start = channel.start

        async def start() -> None:
            if channel.app_id == "cli_one":
                # Readiness is held behind an observed durable renewal instead
                # of a sub-100ms wall-clock schedule.
                await asyncio.wait_for(lease_renewed.wait(), timeout=2.0)
            await original_start()

        channel.start = start
        return channel

    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=slow_factory,
        runtime_leader_fence=_TestRuntimeLeaderFence(),
    )

    try:
        await supervisor.load_active_bindings()

        assert supervisor.running_binding_ids == (first["id"],)
        assert lease_renewed.is_set()
        assert supervisor.health()[first["id"]] == BindingHealth(
            binding_id=first["id"],
            agent_id="pa_1",
            health="healthy",
            detail=None,
            running=True,
        )
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_hung_binding_start_times_out_without_blocking_peer_or_janitor(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, second = supervisor_env
    await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    await repository.activate("pa_2", second["id"], owner_user_id="owner-b")
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_CONVERGENCE_TIMEOUT_SECONDS", 0.3, raising=False)
    blocked = asyncio.Event()
    factory = _Factory(set())

    def blocking_factory(*args: Any, **kwargs: Any) -> _FakeFeishuChannel:
        channel = factory(*args, **kwargs)
        original_start = channel.start

        async def start() -> None:
            if channel.app_id == "cli_one":
                await blocked.wait()
            await original_start()

        channel.start = start
        return channel

    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=blocking_factory,
        runtime_leader_fence=_TestRuntimeLeaderFence(),
    )

    try:
        await asyncio.wait_for(supervisor.load_active_bindings(), timeout=2.0)

        assert supervisor.running_binding_ids == (second["id"],)
        assert supervisor.health()[first["id"]].health == "unhealthy"
        assert supervisor.health()[second["id"]].health == "healthy"
        assert supervisor._cleanup_janitor_task is not None
        failed = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
        assert failed is not None
        assert failed["runtime_lease_token"] is None
    finally:
        blocked.set()
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_non_cooperative_runtime_claim_cannot_block_peer_or_janitor(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, second = supervisor_env
    await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    await repository.activate("pa_2", second["id"], owner_user_id="owner-b")
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_CONVERGENCE_TIMEOUT_SECONDS", 0.2)
    original_claim = repository.claim_runtime
    claim_entered = asyncio.Event()
    claim_cancelled = asyncio.Event()
    release_claim = asyncio.Event()
    claim_finished = asyncio.Event()

    async def non_cooperative_claim(agent_id: str, binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        if binding_id == first["id"]:
            claim_entered.set()
            while not release_claim.is_set():
                try:
                    await release_claim.wait()
                except asyncio.CancelledError:
                    claim_cancelled.set()
            try:
                return await original_claim(agent_id, binding_id, **kwargs)
            finally:
                claim_finished.set()
        return await original_claim(agent_id, binding_id, **kwargs)

    monkeypatch.setattr(repository, "claim_runtime", non_cooperative_claim)
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=_Factory(set()),
        runtime_leader_fence=_TestRuntimeLeaderFence(),
    )
    load_task = asyncio.create_task(supervisor.load_active_bindings())
    done, _pending = await asyncio.wait({load_task}, timeout=0.8)
    try:
        assert claim_entered.is_set()
        assert not claim_cancelled.is_set()
        assert load_task in done
        await load_task
        assert supervisor.running_binding_ids == (second["id"],)
        assert supervisor._cleanup_janitor_task is not None
    finally:
        release_claim.set()
        await asyncio.wait_for(claim_finished.wait(), timeout=1.0)
        if not load_task.done():
            await asyncio.wait_for(load_task, timeout=2.0)
        for _ in range(100):
            retained = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
            if retained is not None and retained["runtime_lease_token"] is None:
                break
            await asyncio.sleep(0.01)
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_late_runtime_claim_reconciles_commit_when_acknowledgement_is_cancelled(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_CONVERGENCE_TIMEOUT_SECONDS", 0.05)
    original_claim = repository.claim_runtime
    claim_committed = asyncio.Event()
    allow_claim_return = asyncio.Event()
    cancellation_seen = asyncio.Event()

    async def commit_before_ack(agent_id: str, binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        claimed = await original_claim(agent_id, binding_id, **kwargs)
        claim_committed.set()
        try:
            await allow_claim_return.wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            raise
        return claimed

    monkeypatch.setattr(repository, "claim_runtime", commit_before_ack)
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    try:
        started = await supervisor.start_binding(first["id"])
        assert started.health == "unhealthy"
        await asyncio.wait_for(claim_committed.wait(), timeout=1.0)
        allow_claim_return.set()
        await _wait_for_runtime_token_clear(repository, first, owner_user_id="owner-a")
        assert not cancellation_seen.is_set()
    finally:
        allow_claim_return.set()
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_predeadline_claim_exception_reconciles_commit_without_blocking_peer(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, second = supervisor_env
    original_claim = repository.claim_runtime
    original_get_for_supervisor = repository.get_for_supervisor
    claim_failed = asyncio.Event()

    async def commit_then_fail(agent_id: str, binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        claimed = await original_claim(agent_id, binding_id, **kwargs)
        if binding_id == first["id"]:
            assert claimed is not None
            claim_failed.set()
            raise RuntimeError("claim acknowledgement lost")
        return claimed

    async def delayed_visibility(binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        current = await original_get_for_supervisor(binding_id, **kwargs)
        if binding_id == first["id"] and claim_failed.is_set() and current is not None:
            current = dict(current)
            current["runtime_lease_token"] = None
        return current

    monkeypatch.setattr(repository, "claim_runtime", commit_then_fail)
    monkeypatch.setattr(repository, "get_for_supervisor", delayed_visibility)
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=_Factory(set()),
        runtime_leader_fence=_TestRuntimeLeaderFence(),
    )
    try:
        with pytest.raises(RuntimeError):
            await supervisor.start_binding(first["id"])

        peer = await supervisor.start_binding(second["id"])
        assert peer.running is True
        await _wait_for_runtime_token_clear(repository, first, owner_user_id="owner-a")
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_reload_claim_acknowledgement_loss_persists_failure_health_without_blocking_peer(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, second = supervisor_env
    await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    await repository.activate("pa_2", second["id"], owner_user_id="owner-b")
    original_claim = repository.claim_runtime

    async def commit_then_fail(agent_id: str, binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        claimed = await original_claim(agent_id, binding_id, **kwargs)
        if binding_id == first["id"]:
            assert claimed is not None
            raise RuntimeError("claim acknowledgement lost")
        return claimed

    monkeypatch.setattr(repository, "claim_runtime", commit_then_fail)
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=_Factory(set()),
        runtime_leader_fence=_TestRuntimeLeaderFence(),
    )
    try:
        await supervisor.load_active_bindings()
        reconciled = await _wait_for_runtime_token_clear(
            repository,
            first,
            owner_user_id="owner-a",
        )
        deadline = asyncio.get_running_loop().time() + 1.0
        while (
            reconciled["health"] != "unhealthy"
            or getattr(
                supervisor.health().get(first["id"]),
                "health",
                None,
            )
            != "unhealthy"
        ) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
            current = await repository.get(
                "pa_1",
                first["id"],
                owner_user_id="owner-a",
            )
            assert current is not None
            reconciled = current

        assert reconciled["health"] == "unhealthy"
        assert supervisor.health()[first["id"]].health == "unhealthy"
        assert supervisor.running_binding_ids == (second["id"],)
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_predeadline_self_cancelled_claim_reconciles_committed_token(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    original_claim = repository.claim_runtime

    async def commit_then_cancel(agent_id: str, binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        claimed = await original_claim(agent_id, binding_id, **kwargs)
        assert claimed is not None
        raise asyncio.CancelledError

    monkeypatch.setattr(repository, "claim_runtime", commit_then_cancel)
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=_Factory(set()),
        runtime_leader_fence=_TestRuntimeLeaderFence(),
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await supervisor.start_binding(first["id"])
        await _wait_for_runtime_token_clear(repository, first, owner_user_id="owner-a")
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_late_runtime_claim_retries_release_after_transient_failure(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_CONVERGENCE_TIMEOUT_SECONDS", 0.05)
    original_claim = repository.claim_runtime
    original_reconcile = repository.reconcile_runtime_claim
    allow_claim = asyncio.Event()
    claim_committed = asyncio.Event()
    release_attempts = 0

    async def delayed_claim(agent_id: str, binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        while not allow_claim.is_set():
            try:
                await allow_claim.wait()
            except asyncio.CancelledError:
                continue
        claimed = await original_claim(agent_id, binding_id, **kwargs)
        claim_committed.set()
        return claimed

    async def transient_reconcile(*args: Any, **kwargs: Any) -> Any:
        nonlocal release_attempts
        release_attempts += 1
        if release_attempts == 1:
            raise RuntimeError("temporary release failure")
        return await original_reconcile(*args, **kwargs)

    monkeypatch.setattr(repository, "claim_runtime", delayed_claim)
    monkeypatch.setattr(repository, "reconcile_runtime_claim", transient_reconcile)
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    try:
        started = await supervisor.start_binding(first["id"])
        assert started.health == "unhealthy"
        allow_claim.set()
        await asyncio.wait_for(claim_committed.wait(), timeout=1.0)
        await _wait_for_runtime_token_clear(repository, first, owner_user_id="owner-a")
        assert release_attempts >= 2
    finally:
        allow_claim.set()
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_late_runtime_claim_atomically_releases_after_same_token_generation_advance(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_CONVERGENCE_TIMEOUT_SECONDS", 0.05)
    original_claim = repository.claim_runtime
    original_reconcile = repository.reconcile_runtime_claim
    allow_claim = asyncio.Event()
    claim_committed = asyncio.Event()
    release_attempts = 0

    async def delayed_claim(agent_id: str, binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        while not allow_claim.is_set():
            try:
                await allow_claim.wait()
            except asyncio.CancelledError:
                continue
        claimed = await original_claim(agent_id, binding_id, **kwargs)
        claim_committed.set()
        return claimed

    async def generation_advance(*args: Any, **kwargs: Any) -> Any:
        nonlocal release_attempts
        release_attempts += 1
        if release_attempts == 1:
            requested = await repository.request_runtime_stop(
                "pa_1",
                first["id"],
                owner_user_id="owner-a",
            )
            assert requested is not None
        return await original_reconcile(*args, **kwargs)

    monkeypatch.setattr(repository, "claim_runtime", delayed_claim)
    monkeypatch.setattr(repository, "reconcile_runtime_claim", generation_advance)
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    try:
        started = await supervisor.start_binding(first["id"])
        assert started.health == "unhealthy"
        allow_claim.set()
        await asyncio.wait_for(claim_committed.wait(), timeout=1.0)
        reconciled = await _wait_for_runtime_token_clear(
            repository,
            first,
            owner_user_id="owner-a",
        )
        assert release_attempts == 1
        assert reconciled["health"] == "unknown"
        assert reconciled["health_detail"] == "Runtime stop is pending"
    finally:
        allow_claim.set()
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_shutdown_keeps_leader_fence_until_late_runtime_claim_is_reconciled(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_CONVERGENCE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS", 0.05, raising=False)
    original_claim = repository.claim_runtime
    claim_entered = asyncio.Event()
    allow_claim = asyncio.Event()
    claim_committed = asyncio.Event()

    async def blocked_claim(agent_id: str, binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        claim_entered.set()
        while not allow_claim.is_set():
            try:
                await allow_claim.wait()
            except asyncio.CancelledError:
                continue
        claimed = await original_claim(agent_id, binding_id, **kwargs)
        claim_committed.set()
        return claimed

    monkeypatch.setattr(repository, "claim_runtime", blocked_claim)
    fence = _TestRuntimeLeaderFence()
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=_Factory(set()),
        runtime_leader_fence=fence,
    )
    try:
        await supervisor.load_active_bindings()
        await asyncio.wait_for(claim_entered.wait(), timeout=1.0)
        with pytest.raises(RuntimeError, match="late runtime claim"):
            await supervisor.shutdown()
        assert fence.held is True
    finally:
        allow_claim.set()
        await asyncio.wait_for(claim_committed.wait(), timeout=1.0)
        await _wait_for_runtime_token_clear(repository, first, owner_user_id="owner-a")
        if fence.held:
            await supervisor.shutdown()
    assert fence.held is False


@pytest.mark.asyncio
async def test_shutdown_remains_retryable_while_quiescing_transport_cannot_stop(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    monkeypatch.setattr(
        "app.channels.supervisor.RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS",
        0.05,
    )
    monkeypatch.setattr(
        "app.channels.supervisor.RUNTIME_LEASE_HEARTBEAT_SECONDS",
        0.01,
    )
    factory = _Factory(
        set(),
        fail_stop_app_ids={"cli_one"},
        fail_stop_running_app_ids={"cli_one"},
    )
    fence = _TestRuntimeLeaderFence()
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=factory,
        runtime_leader_fence=fence,
    )
    await supervisor.load_active_bindings()
    await supervisor.start_binding(first["id"])

    channel = factory.instances[0]
    cleanup_barrier = CleanupRetryBarrier(channel.stop)
    monkeypatch.setattr(channel, "stop", cleanup_barrier.stop)
    retry_completed = False
    test_error: BaseException | None = None
    cleanup_errors: list[BaseException] = []
    try:
        with pytest.raises(RuntimeError, match="runtime ownership"):
            await supervisor.shutdown()
        await asyncio.wait_for(cleanup_barrier.entered.wait(), timeout=1.0)
        assert supervisor._shutdown_complete is False
        assert supervisor.owned_binding_ids == (first["id"],)
        assert fence.held is True

        channel.fail_stop = False
        cleanup_barrier.release.set()
        await asyncio.wait_for(cleanup_barrier.recovered.wait(), timeout=1.0)
        await _wait_for_runtime_token_clear(
            repository,
            first,
            owner_user_id="owner-a",
        )
        await wait_for_supervisor_ownership(supervisor)

        monkeypatch.setattr(
            "app.channels.supervisor.RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS",
            1.0,
        )
        await supervisor.shutdown()
        retry_completed = True
        assert supervisor._shutdown_complete is True
        assert supervisor.owned_binding_ids == ()
        assert fence.held is False
    except BaseException as exc:
        test_error = exc
    finally:
        cleanup_barrier.release.set()
        channel.fail_stop = False
        try:
            if not retry_completed:
                monkeypatch.setattr(
                    "app.channels.supervisor.RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS",
                    1.0,
                )
                await finish_supervisor_cleanup(supervisor, fence=fence)
        except BaseException as exc:
            cleanup_errors.append(exc)

        raise_test_cleanup_errors(
            test_error,
            cleanup_errors,
            message="Supervisor shutdown regression and cleanup both failed",
        )


@pytest.mark.asyncio
async def test_shutdown_drains_transient_runtime_release_failure(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    monkeypatch.setattr(
        "app.channels.supervisor.RUNTIME_LEASE_HEARTBEAT_SECONDS",
        0.01,
    )
    original_release = repository.release_runtime
    release_attempts = 0

    async def transient_release(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        nonlocal release_attempts
        release_attempts += 1
        if release_attempts == 1:
            raise RuntimeError("temporary release failure")
        return await original_release(*args, **kwargs)

    monkeypatch.setattr(repository, "release_runtime", transient_release)
    fence = _TestRuntimeLeaderFence()
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=_Factory(set()),
        runtime_leader_fence=fence,
    )
    await supervisor.load_active_bindings()
    await supervisor.start_binding(first["id"])

    await supervisor.shutdown()

    assert release_attempts >= 2
    assert supervisor._shutdown_complete is True
    assert supervisor.owned_binding_ids == ()
    assert fence.held is False


@pytest.mark.asyncio
async def test_shutdown_remains_retryable_while_runtime_release_keeps_failing(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    monkeypatch.setattr(
        "app.channels.supervisor.RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS",
        0.05,
    )
    monkeypatch.setattr(
        "app.channels.supervisor.RUNTIME_LEASE_HEARTBEAT_SECONDS",
        0.01,
    )
    original_release = repository.release_runtime
    release_attempts = 0
    cleanup_retry_started = asyncio.Event()
    release_cleanup_retry = asyncio.Event()
    release_recovered = asyncio.Event()

    async def failed_release(*_args: Any, **_kwargs: Any) -> None:
        nonlocal release_attempts
        release_attempts += 1
        if release_attempts > 1:
            cleanup_retry_started.set()
            await release_cleanup_retry.wait()
        raise RuntimeError("release unavailable")

    monkeypatch.setattr(repository, "release_runtime", failed_release)
    fence = _TestRuntimeLeaderFence()
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=_Factory(set()),
        runtime_leader_fence=fence,
    )
    await supervisor.load_active_bindings()
    await supervisor.start_binding(first["id"])

    retry_completed = False
    try:
        with pytest.raises(RuntimeError, match="runtime ownership"):
            await supervisor.shutdown()
        await asyncio.wait_for(cleanup_retry_started.wait(), timeout=1.0)
        assert supervisor._shutdown_complete is False
        assert supervisor.owned_binding_ids == (first["id"],)
        assert fence.held is True

        async def recovering_release(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
            released = await original_release(*args, **kwargs)
            release_recovered.set()
            return released

        monkeypatch.setattr(repository, "release_runtime", recovering_release)
        release_cleanup_retry.set()
        await asyncio.wait_for(release_recovered.wait(), timeout=1.0)
        await _wait_for_runtime_token_clear(repository, first, owner_user_id="owner-a")
        for _ in range(100):
            if supervisor.owned_binding_ids == ():
                break
            await asyncio.sleep(0.01)
        assert supervisor.owned_binding_ids == ()
        monkeypatch.setattr(
            "app.channels.supervisor.RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS",
            1.0,
        )
        await supervisor.shutdown()
        retry_completed = True
        assert supervisor._shutdown_complete is True
        assert supervisor.owned_binding_ids == ()
        assert fence.held is False
    finally:
        release_cleanup_retry.set()
        monkeypatch.setattr(repository, "release_runtime", original_release)
        if not retry_completed:
            for _ in range(100):
                if supervisor.owned_binding_ids == ():
                    break
                await asyncio.sleep(0.01)
            if supervisor.owned_binding_ids == () and not supervisor._shutdown_complete:
                monkeypatch.setattr(
                    "app.channels.supervisor.RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS",
                    1.0,
                )
                await supervisor.shutdown()
            elif fence.held:
                await fence.release()


@pytest.mark.asyncio
async def test_non_cooperative_start_cannot_block_peer_startup_or_lose_fencing_owner(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, second = supervisor_env
    await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    await repository.activate("pa_2", second["id"], owner_user_id="owner-b")
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_CONVERGENCE_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_TEARDOWN_TIMEOUT_SECONDS", 0.05, raising=False)
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STOP_TIMEOUT_SECONDS", 0.05, raising=False)
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_LEASE_HEARTBEAT_SECONDS", 0.05)
    start_cancelled = asyncio.Event()
    release_start = asyncio.Event()
    factory = _Factory(set())

    async def connected(_app_id: str, _app_secret: str) -> tuple[bool, str]:
        return True, "connected"

    def non_cooperative_factory(*args: Any, **kwargs: Any) -> _FakeFeishuChannel:
        channel = factory(*args, **kwargs)
        original_start = channel.start

        async def start() -> None:
            if channel.app_id == "cli_one":
                while not release_start.is_set():
                    try:
                        await release_start.wait()
                    except asyncio.CancelledError:
                        start_cancelled.set()
            await original_start()

        channel.start = start
        return channel

    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=non_cooperative_factory,
        connection_tester=connected,
        runtime_leader_fence=_TestRuntimeLeaderFence(),
    )
    load_task = asyncio.create_task(supervisor.load_active_bindings())
    done, _pending = await asyncio.wait({load_task}, timeout=3.0)
    try:
        assert load_task in done
        await load_task
        assert start_cancelled.is_set()
        assert supervisor.health()[first["id"]].health == "unhealthy"
        assert supervisor.health()[second["id"]].health == "healthy"
        assert supervisor.running_binding_ids == (second["id"],)
        assert (await supervisor.test_binding(first["id"])).running is False
        assert supervisor._cleanup_janitor_task is not None
        retained = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
        assert retained is not None and retained["runtime_lease_token"] is not None
        assert factory.instances[0].stop_count == 0
        with pytest.raises(BindingCleanupPendingError):
            await asyncio.wait_for(supervisor.start_binding(first["id"]), timeout=1.0)
    finally:
        release_start.set()
        if not load_task.done():
            await asyncio.wait_for(load_task, timeout=3.0)
        for _ in range(1000):
            if first["id"] not in supervisor.owned_binding_ids:
                break
            await asyncio.sleep(0.01)
        assert first["id"] not in supervisor.owned_binding_ids
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_non_cooperative_startup_stop_cannot_block_peer_or_janitor(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, second = supervisor_env
    await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    await repository.activate("pa_2", second["id"], owner_user_id="owner-b")
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_CONVERGENCE_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_TEARDOWN_TIMEOUT_SECONDS", 0.05, raising=False)
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STOP_TIMEOUT_SECONDS", 0.05, raising=False)
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_LEASE_HEARTBEAT_SECONDS", 0.05)
    stop_started = asyncio.Event()
    release_stop = asyncio.Event()
    factory = _Factory(set())

    async def connected(_app_id: str, _app_secret: str) -> tuple[bool, str]:
        return True, "connected"

    def non_cooperative_stop_factory(*args: Any, **kwargs: Any) -> _FakeFeishuChannel:
        channel = factory(*args, **kwargs)
        original_start = channel.start
        original_stop = channel.stop

        async def start() -> None:
            if channel.app_id == "cli_one":
                await asyncio.Event().wait()
            await original_start()

        async def stop() -> None:
            if channel.app_id == "cli_one":
                stop_started.set()
                while not release_stop.is_set():
                    try:
                        await release_stop.wait()
                    except asyncio.CancelledError:
                        pass
            await original_stop()

        channel.start = start
        channel.stop = stop
        return channel

    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=non_cooperative_stop_factory,
        connection_tester=connected,
        runtime_leader_fence=_TestRuntimeLeaderFence(),
    )
    load_task = asyncio.create_task(supervisor.load_active_bindings())
    done, _pending = await asyncio.wait({load_task}, timeout=3.0)
    try:
        assert load_task in done
        await load_task
        assert stop_started.is_set()
        assert supervisor.health()[first["id"]].health == "unhealthy"
        assert supervisor.health()[second["id"]].health == "healthy"
        assert supervisor.running_binding_ids == (second["id"],)
        assert (await supervisor.test_binding(first["id"])).running is False
        assert supervisor._cleanup_janitor_task is not None
        retained = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
        assert retained is not None and retained["runtime_lease_token"] is not None
        with pytest.raises(BindingCleanupPendingError):
            await asyncio.wait_for(supervisor.start_binding(first["id"]), timeout=1.0)
    finally:
        release_stop.set()
        if not load_task.done():
            await asyncio.wait_for(load_task, timeout=3.0)
        for _ in range(1000):
            if first["id"] not in supervisor.owned_binding_ids:
                break
            await asyncio.sleep(0.01)
        assert first["id"] not in supervisor.owned_binding_ids
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_non_cooperative_failure_projection_cannot_block_peer_or_janitor(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, second = supervisor_env
    await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    await repository.activate("pa_2", second["id"], owner_user_id="owner-b")
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_FAILURE_PROJECTION_TIMEOUT_SECONDS", 0.05)
    original_get = repository.get_for_supervisor
    read_failed = False
    release_projection = asyncio.Event()
    projection_entered = asyncio.Event()
    projection_cancelled = asyncio.Event()

    async def get_for_supervisor(binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        nonlocal read_failed
        if binding_id == first["id"] and not read_failed:
            read_failed = True
            raise RuntimeError("one row read failed")
        return await original_get(binding_id, **kwargs)

    monkeypatch.setattr(repository, "get_for_supervisor", get_for_supervisor)
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=_Factory(set()),
        runtime_leader_fence=_TestRuntimeLeaderFence(),
    )
    original_update_health = repository.update_health

    async def non_cooperative_projection(agent_id: str, binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        if binding_id == first["id"] and kwargs["health"] == "unhealthy":
            projection_entered.set()
            while not release_projection.is_set():
                try:
                    await release_projection.wait()
                except asyncio.CancelledError:
                    projection_cancelled.set()
        return await original_update_health(agent_id, binding_id, **kwargs)

    monkeypatch.setattr(repository, "update_health", non_cooperative_projection)
    load_task = asyncio.create_task(supervisor.load_active_bindings())
    done, _pending = await asyncio.wait({load_task}, timeout=0.6)
    try:
        assert load_task in done
        await load_task
        assert projection_entered.is_set()
        assert projection_cancelled.is_set()
        assert first["id"] not in supervisor.health()
        assert supervisor.health()[second["id"]].health == "healthy"
        assert supervisor._cleanup_janitor_task is not None
    finally:
        release_projection.set()
        if not load_task.done():
            await asyncio.wait_for(load_task, timeout=2.0)
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_detached_failure_projection_cannot_overwrite_new_runtime_generation(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    activated = await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    assert activated is not None
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_FAILURE_PROJECTION_TIMEOUT_SECONDS", 0.05)
    original_get = repository.get_for_supervisor
    original_update_health = repository.update_health
    read_failed = False
    release_projection = asyncio.Event()
    projection_entered = asyncio.Event()
    projection_finished = asyncio.Event()

    async def get_for_supervisor(binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        nonlocal read_failed
        if binding_id == first["id"] and not read_failed:
            read_failed = True
            raise RuntimeError("one row read failed")
        return await original_get(binding_id, **kwargs)

    async def delayed_unhealthy_projection(agent_id: str, binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        if binding_id == first["id"] and kwargs["health"] == "unhealthy":
            projection_entered.set()
            while not release_projection.is_set():
                try:
                    await release_projection.wait()
                except asyncio.CancelledError:
                    pass
            try:
                return await original_update_health(agent_id, binding_id, **kwargs)
            finally:
                projection_finished.set()
        return await original_update_health(agent_id, binding_id, **kwargs)

    monkeypatch.setattr(repository, "get_for_supervisor", get_for_supervisor)
    monkeypatch.setattr(repository, "update_health", delayed_unhealthy_projection)
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=_Factory(set()),
        runtime_leader_fence=_TestRuntimeLeaderFence(),
    )
    try:
        await asyncio.wait_for(supervisor.load_active_bindings(), timeout=0.6)
        await asyncio.wait_for(projection_entered.wait(), timeout=0.2)
        restarted = await asyncio.wait_for(supervisor.start_binding(first["id"]), timeout=0.3)
        assert restarted.running is True

        release_projection.set()
        await asyncio.wait_for(projection_finished.wait(), timeout=0.3)
        stored = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
        assert stored is not None
        assert stored["runtime_generation"] > activated["runtime_generation"]
        assert stored["health"] == "healthy"
        assert supervisor.health()[first["id"]].health == "healthy"
        assert supervisor.health()[first["id"]].running is True
    finally:
        release_projection.set()
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_late_health_observation_cannot_overwrite_newer_same_generation_result(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    original_update_health = repository.update_health
    stale_projection_entered = asyncio.Event()
    release_stale_projection = asyncio.Event()

    async def connected(_app_id: str, _app_secret: str) -> tuple[bool, str]:
        return True, "connected"

    async def delayed_unhealthy(agent_id: str, binding_id: str, **kwargs: Any) -> dict[str, Any] | None:
        if binding_id == first["id"] and kwargs["health"] == "unhealthy":
            stale_projection_entered.set()
            await release_stale_projection.wait()
        return await original_update_health(agent_id, binding_id, **kwargs)

    monkeypatch.setattr(repository, "update_health", delayed_unhealthy)
    factory = _Factory(set())
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=factory,
        connection_tester=connected,
    )
    await supervisor.start_binding(first["id"])
    channel = factory.instances[-1]
    assert channel.runtime_health_callback is not None
    stale_task = asyncio.create_task(channel.runtime_health_callback(False, "older cleanup observation"))
    await asyncio.wait_for(stale_projection_entered.wait(), timeout=1.0)
    try:
        tested = await supervisor.test_binding(first["id"])
        assert tested.health == "healthy"
    finally:
        release_stale_projection.set()
        await asyncio.wait_for(stale_task, timeout=1.0)

    stored = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert stored is not None
    assert stored["health"] == "healthy"
    assert supervisor.health()[first["id"]].health == "healthy"
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_connection_test_returns_current_health_when_restart_stales_probe(
    supervisor_env: SupervisorEnv,
) -> None:
    repository, secrets, first, _second = supervisor_env
    probe_entered = asyncio.Event()
    release_probe = asyncio.Event()

    async def delayed_probe(_app_id: str, _app_secret: str) -> tuple[bool, str]:
        probe_entered.set()
        await release_probe.wait()
        return True, "connected"

    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=_Factory(set()),
        connection_tester=delayed_probe,
    )
    await supervisor.start_binding(first["id"])
    probe_task = asyncio.create_task(supervisor.test_binding(first["id"]))
    await asyncio.wait_for(probe_entered.wait(), timeout=1.0)
    await supervisor.restart_binding(first["id"])
    release_probe.set()
    tested = await asyncio.wait_for(probe_task, timeout=1.0)

    assert tested.health == "healthy"
    assert tested.running is True
    assert supervisor.health()[first["id"]].health == "healthy"
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_cancelled_file_leader_acquire_drains_and_releases_late_lock() -> None:
    from app.channels import supervisor as supervisor_module

    class BlockingLock:
        def __init__(self) -> None:
            self.acquire_started = threading.Event()
            self.allow_acquire = threading.Event()
            self.acquire_settled = threading.Event()
            self.acquired = False
            self.release_count = 0

        def acquire(self, *, timeout: float) -> object:
            assert timeout == 2.0
            self.acquire_started.set()
            self.allow_acquire.wait(timeout=1.0)
            self.acquired = True
            self.acquire_settled.set()
            return object()

        def release(self) -> None:
            self.acquired = False
            self.release_count += 1

    lock = BlockingLock()
    fence = supervisor_module._FileRuntimeLeaderFence()
    fence._lock = lock
    waiter = asyncio.create_task(fence.acquire())
    assert await asyncio.to_thread(lock.acquire_started.wait, 1.0)

    waiter.cancel()
    await asyncio.sleep(0)
    lock.allow_acquire.set()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert await asyncio.to_thread(lock.acquire_settled.wait, 1.0)

    assert lock.acquired is False
    assert lock.release_count == 1
    assert fence._held is False


@pytest.mark.asyncio
async def test_gateway_janitor_recovers_cleanup_for_inactive_binding(
    supervisor_env: tuple[AgentChannelRepository, LocalEncryptedSecretStore, dict[str, Any], dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import deerflow.config.paths as paths_module

    repository, secrets, first, _second = supervisor_env
    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path / "runtime"))
    monkeypatch.setattr(
        "app.channels.feishu.FEISHU_ATTACHMENT_CLEANUP_RETRY_INTERVAL_SECONDS",
        0.01,
    )

    class _MountedProvider:
        uses_thread_data_mounts = True

        async def acquire_with_lease_async(self, thread_id: str, *, user_id: str) -> SandboxAcquisition:
            return SandboxAcquisition(
                sandbox_id=f"local:{thread_id}",
                acquisition_token="janitor-acquire",
                thread_id=thread_id,
            )

        def accept_acquisition(self, _acquisition: SandboxAcquisition) -> None:
            return None

        def abandon_acquisition(self, _acquisition: SandboxAcquisition) -> None:
            return None

    monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", _MountedProvider)
    host_path = get_paths().sandbox_uploads_dir("thread-janitor", user_id="owner-a") / "input.bin"
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_bytes(b"pending")
    outbox_path = tmp_path / "runtime" / "published-attachment-cleanup" / "janitor-job.json"
    outbox_path.parent.mkdir(parents=True, exist_ok=True)
    outbox_path.write_text(
        json.dumps(
            {
                "job_id": "janitor-job",
                "binding_id": first["id"],
                "thread_id": "thread-janitor",
                "owner_user_id": "owner-a",
                "virtual_paths": ["/mnt/user-data/uploads/input.bin"],
                "phase": "producer_pending",
                "producer_token": "dead-process-producer",
                "producer_lease_expires_at": 0.0,
                "claim_token": None,
                "claim_lease_expires_at": None,
                "version": 1,
            }
        ),
        encoding="utf-8",
    )
    deleting = await repository.mark_deleting("pa_1", first["id"], owner_user_id="owner-a")
    assert deleting is not None
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))

    try:
        await supervisor.recover_cleanup_state()
        assert not outbox_path.exists()
        assert not host_path.exists()
        assert await repository.get("pa_1", first["id"], owner_user_id="owner-a") is None
        with pytest.raises(KeyError):
            await secrets.get(first["secret_ref"])
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_cleanup_pass_retains_legacy_pending_ingest_during_rolling_upgrade(
    supervisor_env: tuple[AgentChannelRepository, LocalEncryptedSecretStore, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    monkeypatch.setattr("deerflow.publishing.secret_store.SECRET_PENDING_INGEST_GRACE_SECONDS", 0.0)
    pending_ref = await secrets.put_pending(
        "uncommitted-candidate",
        agent_id="pa_1",
        binding_id=first["id"],
        owner_user_id="owner-a",
    )
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))

    await supervisor.recover_cleanup_state()

    assert await secrets.get(pending_ref) == "uncommitted-candidate"
    assert [record.secret_ref for record in await secrets.list_pending()] == [pending_ref]


@pytest.mark.asyncio
async def test_expired_secret_writer_remains_db_owned_until_late_ciphertext_is_erased(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    secret_ref = secrets.new_ref()
    reserved = await repository.reserve_secret_ingest(
        agent_id="pa_1",
        binding_id=first["id"],
        owner_user_id="owner-a",
        secret_ref=secret_ref,
        defer_seconds=0,
    )
    assert reserved is not None
    writing = await repository.begin_secret_ingest_write(
        secret_ref,
        agent_id="pa_1",
        binding_id=first["id"],
        owner_user_id="owner-a",
        writer_token="paused-writer",
        lease_seconds=0.01,
    )
    assert writing is not None
    original_claim = repository.claim_secret_ingest_cleanup

    async def short_cleanup_claim(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        return await original_claim(*args, **kwargs, lease_seconds=0.01)

    monkeypatch.setattr(repository, "claim_secret_ingest_cleanup", short_cleanup_claim)
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    await asyncio.sleep(0.11)

    await supervisor.recover_cleanup_state()
    await secrets.put_reserved(secret_ref, "late-ciphertext")
    await asyncio.sleep(0.11)
    await supervisor.recover_cleanup_state()

    with pytest.raises(KeyError):
        await secrets.get(secret_ref)
    assert await repository.list_secret_ingests_due(supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE) == []


@pytest.mark.asyncio
async def test_pending_ingest_snapshot_cannot_delete_ref_after_rotation_takes_ownership(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    candidate_ref = secrets.new_ref()
    reserved = await repository.reserve_secret_ingest(
        agent_id="pa_1",
        binding_id=first["id"],
        owner_user_id="owner-a",
        secret_ref=candidate_ref,
        defer_seconds=0,
    )
    assert reserved is not None
    writing = await repository.begin_secret_ingest_write(
        candidate_ref,
        agent_id="pa_1",
        binding_id=first["id"],
        owner_user_id="owner-a",
        writer_token="candidate-writer",
    )
    assert writing is not None
    await secrets.put_reserved(
        candidate_ref,
        encode_feishu_credentials(
            FeishuCredentials(
                app_secret="candidate-secret",
                verification_token="candidate-token",
                encrypt_key="candidate-key",
            )
        ),
    )
    assert await repository.complete_secret_ingest_write(
        candidate_ref,
        writer_token="candidate-writer",
        writer_generation=writing["writer_generation"],
    )
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    snapshot_taken = asyncio.Event()
    release_snapshot = asyncio.Event()
    original_list = repository.list_secret_ingests_due

    async def pause_after_snapshot(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        rows = await original_list(*args, **kwargs)
        snapshot_taken.set()
        await release_snapshot.wait()
        return rows

    monkeypatch.setattr(repository, "list_secret_ingests_due", pause_after_snapshot)
    recovery = asyncio.create_task(supervisor.recover_cleanup_state())
    await asyncio.wait_for(snapshot_taken.wait(), timeout=1.0)

    await supervisor.rotate_binding_credentials(
        "pa_1",
        first["id"],
        owner_user_id="owner-a",
        app_id="cli-candidate",
        secret_ref=candidate_ref,
    )
    release_snapshot.set()
    await recovery

    current = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert current is not None
    assert current["secret_ref"] == candidate_ref
    assert await secrets.get(candidate_ref)


@pytest.mark.asyncio
async def test_pending_attachment_cleanup_marks_running_binding_unhealthy(supervisor_env: SupervisorEnv) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set(), cleanup_unhealthy_app_ids={"cli_one"})
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)

    health = await supervisor.start_binding(first["id"])

    assert health.running is True
    assert health.health == "unhealthy"
    assert health.detail == "Attachment cleanup recovery is pending"
    persisted = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert persisted is not None
    assert persisted["health"] == "unhealthy"


@pytest.mark.asyncio
async def test_runtime_attachment_cleanup_health_updates_without_stopping_binding(supervisor_env: SupervisorEnv) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set())
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    channel = factory.instances[-1]

    assert channel.runtime_health_callback is not None
    await channel.runtime_health_callback(False, "Attachment cleanup recovery is pending")

    assert supervisor.running_binding_ids == (first["id"],)
    assert channel.is_running is True
    assert supervisor.health()[first["id"]].health == "unhealthy"
    persisted = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert persisted is not None
    assert persisted["health"] == "unhealthy"

    await channel.runtime_health_callback(True, None)
    assert supervisor.running_binding_ids == (first["id"],)
    assert supervisor.health()[first["id"]].health == "healthy"


@pytest.mark.asyncio
async def test_restart_rebuilds_channel_with_rotated_secret(supervisor_env: SupervisorEnv) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set())
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    old_instance = factory.instances[-1]
    new_ref = await secrets.put(
        encode_feishu_credentials(
            FeishuCredentials(
                app_secret="secret-rotated",
                verification_token="token-rotated",
                encrypt_key="encrypt-rotated",
            )
        )
    )
    await repository.update_credentials(
        "pa_1",
        first["id"],
        owner_user_id="owner-a",
        app_id="cli_rotated",
        secret_ref=new_ref,
    )

    await supervisor.restart_binding(first["id"])

    assert old_instance.stop_count == 1
    assert factory.instances[-1] is not old_instance
    assert factory.instances[-1].app_id == "cli_rotated"
    assert factory.instances[-1].app_secret == "secret-rotated"
    assert factory.instances[-1].verification_token == "token-rotated"
    assert factory.instances[-1].encrypt_key == "encrypt-rotated"


@pytest.mark.asyncio
async def test_runtime_connection_failure_marks_only_that_binding_unhealthy(supervisor_env: SupervisorEnv) -> None:
    repository, secrets, first, second = supervisor_env
    factory = _Factory(set())
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    await supervisor.start_binding(second["id"])

    await factory.instances[0].runtime_error_callback("Feishu WebSocket connection lost")

    assert set(supervisor.running_binding_ids) == {second["id"]}
    assert supervisor.health()[first["id"]].health == "unhealthy"
    assert supervisor.health()[second["id"]].health == "healthy"
    assert (await repository.get("pa_1", first["id"], owner_user_id="owner-a"))["health"] == "unhealthy"


@pytest.mark.asyncio
async def test_stale_error_from_replaced_runtime_does_not_poison_new_generation(supervisor_env: SupervisorEnv) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set())
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    old_instance = factory.instances[-1]

    await supervisor.restart_binding(first["id"])
    await old_instance.runtime_error_callback("late stale failure")

    assert supervisor.running_binding_ids == (first["id"],)
    assert supervisor.health()[first["id"]].health == "healthy"
    assert factory.instances[-1] is not old_instance
    assert factory.instances[-1].is_running is True


@pytest.mark.asyncio
async def test_stop_failure_preserves_active_runtime_and_status(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    monkeypatch.setattr(
        "app.channels.supervisor.RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS",
        0.05,
    )
    monkeypatch.setattr(
        "app.channels.supervisor.RUNTIME_LEASE_HEARTBEAT_SECONDS",
        0.01,
    )
    factory = _Factory(set(), {"cli_one"}, {"cli_one"})
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    channel = factory.instances[-1]
    cleanup_barrier = CleanupRetryBarrier(channel.stop)
    channel.stop = cleanup_barrier.stop
    health_task: asyncio.Task[None] | None = None
    retry_completed = False
    test_error: BaseException | None = None
    cleanup_errors: list[BaseException] = []
    try:
        with pytest.raises(BindingCleanupPendingError):
            await supervisor.stop_binding(first["id"])

        await asyncio.wait_for(cleanup_barrier.entered.wait(), timeout=1.0)
        assert supervisor.running_binding_ids == ()
        assert supervisor.owned_binding_ids == (first["id"],)
        retained = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
        assert retained is not None
        assert retained["status"] == "active"
        assert retained["runtime_stop_requested"] is True
        assert retained["runtime_lease_token"] is not None
        assert channel.runtime_health_callback is not None
        health_task = asyncio.create_task(channel.runtime_health_callback(False, "cleanup still pending"))
        await asyncio.sleep(0)
        assert health_task.done() is False
        cleanup_barrier.release.set()
        await health_task
        assert supervisor.health()[first["id"]].running is False
        with pytest.raises(RuntimeError, match="runtime ownership"):
            await supervisor.shutdown()
        assert supervisor._shutdown_complete is False
        assert supervisor.owned_binding_ids == (first["id"],)

        channel.fail_stop = False
        await asyncio.wait_for(cleanup_barrier.recovered.wait(), timeout=1.0)
        await _wait_for_runtime_token_clear(
            repository,
            first,
            owner_user_id="owner-a",
        )
        await wait_for_supervisor_ownership(supervisor)

        monkeypatch.setattr(
            "app.channels.supervisor.RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS",
            1.0,
        )
        await supervisor.shutdown()
        retry_completed = True
        assert supervisor._shutdown_complete is True
        assert supervisor.owned_binding_ids == ()
    except BaseException as exc:
        test_error = exc
    finally:
        cleanup_barrier.release.set()
        channel.fail_stop = False
        try:
            if health_task is not None and not health_task.done():
                await health_task
            if not retry_completed:
                monkeypatch.setattr(
                    "app.channels.supervisor.RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS",
                    1.0,
                )
                await finish_supervisor_cleanup(supervisor)
        except BaseException as exc:
            cleanup_errors.append(exc)

        raise_test_cleanup_errors(
            test_error,
            cleanup_errors,
            message="Stop-failure regression and cleanup both failed",
        )


@pytest.mark.asyncio
async def test_shutdown_preserves_desired_active_status_for_next_startup(supervisor_env: SupervisorEnv) -> None:
    repository, secrets, first, _second = supervisor_env
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    await supervisor.start_binding(first["id"])

    await supervisor.shutdown()

    assert supervisor.running_binding_ids == ()
    assert (await repository.get("pa_1", first["id"], owner_user_id="owner-a"))["status"] == "active"


@pytest.mark.asyncio
async def test_rotation_start_failure_restores_previous_row_and_runtime(supervisor_env: SupervisorEnv) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory({"cli-broken"})
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    old_runtime = factory.instances[-1]
    new_ref = secrets.new_ref()
    reserved = await repository.reserve_secret_ingest(
        agent_id="pa_1",
        binding_id=first["id"],
        owner_user_id="owner-a",
        secret_ref=new_ref,
    )
    assert reserved is not None
    writing = await repository.begin_secret_ingest_write(
        new_ref,
        agent_id="pa_1",
        binding_id=first["id"],
        owner_user_id="owner-a",
        writer_token="broken-writer",
    )
    assert writing is not None
    await secrets.put_reserved(
        new_ref,
        encode_feishu_credentials(
            FeishuCredentials(
                app_secret="broken-secret",
                verification_token="broken-token",
                encrypt_key="broken-key",
            )
        ),
    )
    assert await repository.complete_secret_ingest_write(
        new_ref,
        writer_token="broken-writer",
        writer_generation=writing["writer_generation"],
    )

    with pytest.raises(BindingStartError):
        await supervisor.rotate_binding_credentials(
            "pa_1",
            first["id"],
            owner_user_id="owner-a",
            app_id="cli-broken",
            secret_ref=new_ref,
        )

    restored = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert restored is not None
    assert restored["app_id"] == first["app_id"]
    assert restored["secret_ref"] == first["secret_ref"]
    assert old_runtime.stop_count == 1
    assert factory.instances[-1].app_id == first["app_id"]
    assert factory.instances[-1].is_running is True
    assert supervisor.running_binding_ids == (first["id"],)
    assert await secrets.get(first["secret_ref"])
    assert await secrets.get(new_ref)


@pytest.mark.asyncio
async def test_inactive_rotation_rejects_health_from_old_credential_probe(supervisor_env: SupervisorEnv) -> None:
    repository, secrets, first, _second = supervisor_env
    probe_entered = asyncio.Event()
    allow_probe = asyncio.Event()

    async def delayed_old_probe(_app_id: str, app_secret: str) -> tuple[bool, str]:
        assert app_secret == "secret-one"
        probe_entered.set()
        await allow_probe.wait()
        return True, "old credentials connected"

    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=_Factory(set()),
        connection_tester=delayed_old_probe,
    )
    new_ref = await _prepare_rotation_secret(repository, secrets, first, owner_user_id="owner-a")
    probe_task = asyncio.create_task(supervisor.test_binding(first["id"]))
    await asyncio.wait_for(probe_entered.wait(), timeout=1.0)
    try:
        await supervisor.rotate_binding_credentials(
            "pa_1",
            first["id"],
            owner_user_id="owner-a",
            app_id="cli-rotated-inactive",
            secret_ref=new_ref,
        )
        allow_probe.set()
        tested = await asyncio.wait_for(probe_task, timeout=1.0)
        current = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
        assert current is not None
        assert current["secret_ref"] == new_ref
        assert current["health"] == "unknown"
        assert tested.health == "unknown"
        assert first["id"] not in supervisor.health()
    finally:
        allow_probe.set()
        await asyncio.gather(probe_task, return_exceptions=True)
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_active_rotation_switches_health_epoch_before_runtime_restart(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    probe_entered = asyncio.Event()
    allow_probe = asyncio.Event()
    credentials_committed = asyncio.Event()
    allow_rotation = asyncio.Event()

    async def delayed_old_probe(_app_id: str, app_secret: str) -> tuple[bool, str]:
        assert app_secret == "secret-one"
        probe_entered.set()
        await allow_probe.wait()
        return False, "old credentials rejected"

    factory = _Factory(set())
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=factory,
        connection_tester=delayed_old_probe,
    )
    await supervisor.start_binding(first["id"])
    new_ref = await _prepare_rotation_secret(repository, secrets, first, owner_user_id="owner-a")
    original_update_credentials = repository.update_credentials

    async def pause_after_update(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        updated = await original_update_credentials(*args, **kwargs)
        if kwargs.get("app_id") == "cli-rotated-active":
            credentials_committed.set()
            await allow_rotation.wait()
        return updated

    monkeypatch.setattr(repository, "update_credentials", pause_after_update)
    probe_task = asyncio.create_task(supervisor.test_binding(first["id"]))
    await asyncio.wait_for(probe_entered.wait(), timeout=1.0)
    rotation_task = asyncio.create_task(
        supervisor.rotate_binding_credentials(
            "pa_1",
            first["id"],
            owner_user_id="owner-a",
            app_id="cli-rotated-active",
            secret_ref=new_ref,
        )
    )
    await asyncio.wait_for(credentials_committed.wait(), timeout=1.0)
    try:
        allow_probe.set()
        tested = await asyncio.wait_for(probe_task, timeout=1.0)
        current = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
        assert current is not None
        assert current["secret_ref"] == new_ref
        assert current["health"] == "unknown"
        assert tested.health == "unknown"
        assert supervisor.health()[first["id"]].health != "unhealthy"
    finally:
        allow_probe.set()
        allow_rotation.set()
        await asyncio.gather(probe_task, return_exceptions=True)
        await rotation_task
        await supervisor.shutdown()

    final = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert final is not None
    assert final["health"] == "healthy"


@pytest.mark.asyncio
async def test_delete_mark_failure_leaves_active_runtime_manageable(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    await supervisor.start_binding(first["id"])

    async def fail_mark(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(repository, "mark_deleting", fail_mark, raising=False)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await supervisor.delete_binding("pa_1", first["id"], owner_user_id="owner-a")

    retained = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert retained is not None
    assert retained["status"] == "active"
    assert supervisor.running_binding_ids == (first["id"],)
    assert await secrets.get(first["secret_ref"])


@pytest.mark.asyncio
async def test_delete_partial_stop_failure_retains_original_fenced_runtime(
    supervisor_env: tuple[AgentChannelRepository, LocalEncryptedSecretStore, dict[str, Any], dict[str, Any]],
) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set(), {"cli_one"})
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    old_runtime = factory.instances[-1]

    with pytest.raises(BindingCleanupPendingError):
        await supervisor.delete_binding("pa_1", first["id"], owner_user_id="owner-a")

    retained = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert retained is not None
    assert retained["status"] == "deleting"
    assert retained["runtime_stop_requested"] is True
    assert retained["runtime_lease_token"] is not None
    assert old_runtime.is_running is False
    assert factory.instances == [old_runtime]
    assert supervisor.running_binding_ids == ()
    assert supervisor.owned_binding_ids == (first["id"],)


@pytest.mark.asyncio
async def test_delete_live_transport_stop_failure_keeps_single_fenced_runtime(
    supervisor_env: SupervisorEnv,
) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(
        set(),
        fail_stop_app_ids={"cli_one"},
        fail_stop_running_app_ids={"cli_one"},
    )
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    original_runtime = factory.instances[-1]

    with pytest.raises(BindingCleanupPendingError):
        await supervisor.delete_binding("pa_1", first["id"], owner_user_id="owner-a")

    retained = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert retained is not None
    assert retained["status"] == "deleting"
    assert retained["runtime_lease_token"] is not None
    assert factory.instances == [original_runtime]
    assert original_runtime.is_running is True
    assert supervisor.running_binding_ids == ()
    assert supervisor.owned_binding_ids == (first["id"],)


@pytest.mark.asyncio
async def test_remote_stop_waits_while_owner_transport_cannot_exit(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(
        set(),
        fail_stop_app_ids={"cli_one"},
        fail_stop_running_app_ids={"cli_one"},
    )
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_LEASE_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_LEASE_TTL_SECONDS", 0.1)
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_LEASE_RELEASE_WAIT_SECONDS", 0.06)
    owner = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    remote = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    await owner.start_binding(first["id"])

    with pytest.raises(BindingCleanupPendingError):
        await remote.stop_binding(first["id"])

    retained = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert retained is not None
    assert retained["status"] == "active"
    assert retained["runtime_stop_requested"] is True
    assert retained["runtime_lease_token"] is not None
    assert len(factory.instances) == 1
    assert factory.instances[0].is_running is True

    factory.instances[0].fail_stop = False
    for _ in range(100):
        if owner.owned_binding_ids == ():
            break
        await asyncio.sleep(0.01)
    assert owner.running_binding_ids == ()
    await remote.stop_binding(first["id"])
    stopped = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert stopped is not None
    assert stopped["status"] == "inactive"
    assert stopped["runtime_lease_token"] is None
    assert owner.running_binding_ids == ()


@pytest.mark.asyncio
async def test_start_claims_runtime_before_opening_transport(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    lease_claimed = asyncio.Event()
    original_claim = repository.claim_runtime

    async def observe_claim(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        claimed = await original_claim(*args, **kwargs)
        if claimed is not None:
            lease_claimed.set()
        return claimed

    class ClaimAwareChannel(_FakeFeishuChannel):
        async def start(self) -> None:
            if not lease_claimed.is_set():
                raise RuntimeError("transport opened before runtime lease")
            await super().start()

    class ClaimAwareFactory(_Factory):
        def __call__(self, bus: MessageBus, **kwargs: Any) -> _FakeFeishuChannel:
            channel = ClaimAwareChannel(bus, **kwargs)
            self.instances.append(channel)
            return channel

    monkeypatch.setattr(repository, "claim_runtime", observe_claim)
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=ClaimAwareFactory(set()))

    health = await supervisor.start_binding(first["id"])

    assert health.health == "healthy"
    assert supervisor.running_binding_ids == (first["id"],)


@pytest.mark.asyncio
async def test_unpublished_stop_failure_retains_local_and_database_owner(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(
        set(),
        fail_stop_app_ids={"cli_one"},
        fail_stop_running_app_ids={"cli_one"},
    )
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_LEASE_HEARTBEAT_SECONDS", 0.01)

    async def revoke_confirmation(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(repository, "confirm_runtime", revoke_confirmation)
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)

    with pytest.raises(BindingStartError):
        await supervisor.start_binding(first["id"])

    retained = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert retained is not None
    assert retained["runtime_lease_token"] is not None
    assert supervisor.running_binding_ids == ()
    assert supervisor.owned_binding_ids == (first["id"],)
    assert factory.instances[0].is_running is True

    factory.instances[0].fail_stop = False
    for _ in range(100):
        if supervisor.owned_binding_ids == ():
            break
        await asyncio.sleep(0.01)
    assert supervisor.running_binding_ids == ()


@pytest.mark.asyncio
async def test_restart_stop_failure_that_clears_ready_state_keeps_single_retry_owner(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set(), fail_stop_app_ids={"cli_one"})
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_LEASE_HEARTBEAT_SECONDS", 0.1)
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    original = factory.instances[0]

    with pytest.raises(RuntimeError, match="connection did not close"):
        await supervisor.restart_binding(first["id"])

    assert original.is_running is False
    assert supervisor._running[first["id"]].cleanup_task is not None
    with pytest.raises(BindingCleanupPendingError):
        await supervisor.start_binding(first["id"])
    assert factory.instances == [original]
    retained = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert retained is not None
    assert retained["runtime_lease_token"] is not None

    original.fail_stop = False
    for _ in range(100):
        retained = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
        if supervisor.running_binding_ids == () and retained is not None and retained["runtime_lease_token"] is None:
            break
        await asyncio.sleep(0.01)

    assert supervisor.running_binding_ids == ()
    assert retained is not None
    assert retained["runtime_lease_token"] is None
    await supervisor.start_binding(first["id"])
    assert len(factory.instances) == 2
    assert factory.instances[-1].is_running is True


@pytest.mark.asyncio
async def test_process_kill_releases_leader_fence_and_recovers_stale_runtime_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    database_path = tmp_path / "runtime.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            PublishedAgentRow(
                id="pa_crash",
                owner_user_id="owner-a",
                slug="crash",
                display_name="Crash",
                status="published",
            )
        )
        await session.commit()
    repository = AgentChannelRepository(session_factory)
    secrets = LocalEncryptedSecretStore(tmp_path / "secrets", key=Fernet.generate_key())
    secret_ref = await secrets.put(
        encode_feishu_credentials(
            FeishuCredentials(
                app_secret="secret-crash",
                verification_token="token-crash",
                encrypt_key="encrypt-crash",
            )
        )
    )
    first = await repository.create(
        agent_id="pa_crash",
        owner_user_id="owner-a",
        app_id="cli_crash",
        secret_ref=secret_ref,
    )
    await repository.activate("pa_crash", first["id"], owner_user_id="owner-a")

    lock_path = tmp_path / "published-feishu-supervisor.lock"
    ready_path = tmp_path / "leader-ready"
    process = subprocess.Popen(
        [
            getattr(sys, "_base_executable", sys.executable),
            "-c",
            (
                "import os,pathlib,sys,time\n"
                "handle=open(sys.argv[1], 'a+')\n"
                "if os.name == 'nt':\n"
                " import msvcrt\n"
                " handle.seek(0); handle.write('0'); handle.flush(); handle.seek(0)\n"
                " msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)\n"
                "else:\n"
                " import fcntl\n"
                " fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                "import sqlite3\n"
                "database=sqlite3.connect(sys.argv[3])\n"
                "database.execute(\"UPDATE agent_channels SET runtime_lease_token='crashed-gateway-token', runtime_lease_expires_at=datetime('now', '+1 hour'), runtime_generation=runtime_generation+1 WHERE id=?\", (sys.argv[4],))\n"
                "database.commit(); database.close()\n"
                "pathlib.Path(sys.argv[2]).touch()\n"
                "time.sleep(3600)\n"
            ),
            str(lock_path),
            str(ready_path),
            str(database_path),
            first["id"],
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(500):
            if ready_path.exists():
                break
            await asyncio.sleep(0.01)
        assert ready_path.exists()
        crashed = await repository.get("pa_crash", first["id"], owner_user_id="owner-a")
        assert crashed is not None
        assert crashed["runtime_lease_token"] == "crashed-gateway-token"
        blocked = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
        with pytest.raises(BindingStartError, match="leader"):
            await blocked.load_active_bindings()

        process.terminate()
        await asyncio.to_thread(process.wait, 5.0)
        assert process.poll() is not None

        factory = _Factory(set())
        restarted = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
        await restarted.load_active_bindings()
        try:
            recovered = await repository.get("pa_crash", first["id"], owner_user_id="owner-a")
            assert recovered is not None
            assert recovered["runtime_lease_token"] != "crashed-gateway-token"
            assert restarted.running_binding_ids == (first["id"],)
            assert len(factory.instances) == 1
        finally:
            await restarted.shutdown()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        await engine.dispose()


@pytest.mark.asyncio
async def test_lost_runtime_token_still_stops_local_transport(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set())
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_LEASE_HEARTBEAT_SECONDS", 0.01)
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    lease_task = supervisor._running[first["id"]].lease_task
    assert lease_task is not None

    async def lost(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(repository, "renew_runtime", lost)
    monkeypatch.setattr(repository, "renew_quiescing_runtime", lost)

    for _ in range(100):
        if factory.instances[0].stop_count and supervisor.running_binding_ids == ():
            break
        await asyncio.sleep(0.01)

    assert factory.instances[0].stop_count == 1
    assert factory.instances[0].is_running is False
    assert supervisor.running_binding_ids == ()
    await asyncio.gather(lease_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_runtime_renewal_error_stops_local_transport_fail_closed(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set())
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_LEASE_HEARTBEAT_SECONDS", 0.01)
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    lease_task = supervisor._running[first["id"]].lease_task
    assert lease_task is not None

    async def database_partition(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("database partition")

    monkeypatch.setattr(repository, "renew_runtime", database_partition)
    for _ in range(100):
        if factory.instances[0].stop_count and supervisor.owned_binding_ids == ():
            break
        await asyncio.sleep(0.01)

    assert factory.instances[0].stop_count == 1
    assert factory.instances[0].is_running is False
    assert supervisor.running_binding_ids == ()
    await asyncio.gather(lease_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_remote_stop_never_uses_lease_expiry_as_transport_ack(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set())
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_LEASE_TTL_SECONDS", 0.05)
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_LEASE_RELEASE_WAIT_SECONDS", 0.1)
    owner = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    remote = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    await owner.start_binding(first["id"])
    running = owner._running[first["id"]]
    assert running.lease_task is not None
    running.lease_task.cancel()
    await asyncio.gather(running.lease_task, return_exceptions=True)
    running.lease_task = None

    with pytest.raises(BindingCleanupPendingError):
        await remote.stop_binding(first["id"])

    retained = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert retained is not None
    assert retained["runtime_lease_token"] == running.lease_token
    assert factory.instances[0].is_running is True


@pytest.mark.asyncio
async def test_delete_stop_failure_is_retried_from_durable_tombstone(
    supervisor_env: tuple[AgentChannelRepository, LocalEncryptedSecretStore, dict[str, Any], dict[str, Any]],
) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set(), {"cli_one"})
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    with pytest.raises(BindingCleanupPendingError):
        await supervisor.delete_binding("pa_1", first["id"], owner_user_id="owner-a")

    retained = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert retained is not None
    assert retained["status"] == "deleting"
    assert retained["runtime_lease_token"] is not None
    assert supervisor.running_binding_ids == ()
    assert supervisor.owned_binding_ids == (first["id"],)

    factory.instances[0].fail_stop = False
    await supervisor.recover_cleanup_state()

    assert await repository.get("pa_1", first["id"], owner_user_id="owner-a") is None
    assert supervisor.running_binding_ids == ()


@pytest.mark.asyncio
async def test_delete_secret_failure_keeps_durable_tombstone_for_startup_retry(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    await supervisor.start_binding(first["id"])
    original_delete = secrets.delete
    delete_attempts = 0

    async def fail_once(secret_ref: str) -> bool:
        nonlocal delete_attempts
        delete_attempts += 1
        if delete_attempts == 1:
            raise RuntimeError("secret store unavailable")
        return await original_delete(secret_ref)

    monkeypatch.setattr(secrets, "delete", fail_once)
    with pytest.raises(RuntimeError, match="secret store unavailable"):
        await supervisor.delete_binding("pa_1", first["id"], owner_user_id="owner-a")

    tombstone = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert tombstone is not None
    assert tombstone["status"] == "deleting"
    assert tombstone["delete_previous_status"] == "active"
    assert supervisor.running_binding_ids == ()
    assert await secrets.get(first["secret_ref"])
    with pytest.raises(BindingCleanupPendingError):
        await supervisor.start_binding(first["id"])

    restarted = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    await restarted.load_active_bindings()
    try:
        assert await repository.get("pa_1", first["id"], owner_user_id="owner-a") is None
        with pytest.raises(KeyError):
            await secrets.get(first["secret_ref"])
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_delete_database_failure_retains_tombstone_until_restart(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    await supervisor.start_binding(first["id"])
    original_delete = repository.delete
    delete_attempts = 0

    async def fail_once(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        nonlocal delete_attempts
        delete_attempts += 1
        if delete_attempts == 1:
            raise RuntimeError("database delete unavailable")
        return await original_delete(*args, **kwargs)

    monkeypatch.setattr(repository, "delete", fail_once)
    with pytest.raises(RuntimeError, match="database delete unavailable"):
        await supervisor.delete_binding("pa_1", first["id"], owner_user_id="owner-a")

    tombstone = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert tombstone is not None
    assert tombstone["status"] == "deleting"
    assert supervisor.running_binding_ids == ()
    with pytest.raises(KeyError):
        await secrets.get(first["secret_ref"])

    restarted = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    await restarted.load_active_bindings()
    try:
        assert await repository.get("pa_1", first["id"], owner_user_id="owner-a") is None
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_delete_none_result_retains_tombstone_for_retry(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    await supervisor.start_binding(first["id"])
    original_delete = repository.delete

    async def return_none(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(repository, "delete", return_none)
    with pytest.raises(RuntimeError, match="tombstone"):
        await supervisor.delete_binding("pa_1", first["id"], owner_user_id="owner-a")

    tombstone = await repository.get("pa_1", first["id"], owner_user_id="owner-a")
    assert tombstone is not None
    assert tombstone["status"] == "deleting"
    assert supervisor.running_binding_ids == ()
    monkeypatch.setattr(repository, "delete", original_delete)
    await supervisor.delete_binding("pa_1", first["id"], owner_user_id="owner-a")
    assert await repository.get("pa_1", first["id"], owner_user_id="owner-a") is None


@pytest.mark.asyncio
async def test_shutdown_waits_for_blocked_start_and_rejects_late_registration(supervisor_env: SupervisorEnv) -> None:
    repository, secrets, first, _second = supervisor_env
    start_entered = asyncio.Event()
    release_start = asyncio.Event()
    factory = _Factory(set())

    def blocking_factory(*args: Any, **kwargs: Any) -> _FakeFeishuChannel:
        channel = factory(*args, **kwargs)
        original_start = channel.start

        async def start() -> None:
            start_entered.set()
            await release_start.wait()
            await original_start()

        channel.start = start
        return channel

    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=blocking_factory)
    start_task = asyncio.create_task(supervisor.start_binding(first["id"]))
    await asyncio.wait_for(start_entered.wait(), timeout=1.0)
    shutdown_task = asyncio.create_task(supervisor.shutdown())
    await asyncio.sleep(0)
    assert not shutdown_task.done()

    release_start.set()
    with pytest.raises(RuntimeError, match="shutting down"):
        await start_task
    await asyncio.wait_for(shutdown_task, timeout=1.0)

    assert supervisor.running_binding_ids == ()
    assert factory.instances[-1].is_running is False


@pytest.mark.asyncio
async def test_start_ready_rechecks_cross_process_deletion_tombstone(supervisor_env: SupervisorEnv) -> None:
    repository, secrets, first, _second = supervisor_env
    start_entered = asyncio.Event()
    release_start = asyncio.Event()
    factory = _Factory(set())

    def blocking_factory(*args: Any, **kwargs: Any) -> _FakeFeishuChannel:
        channel = factory(*args, **kwargs)
        original_start = channel.start

        async def start() -> None:
            start_entered.set()
            await release_start.wait()
            await original_start()

        channel.start = start
        return channel

    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=blocking_factory)
    start_task = asyncio.create_task(supervisor.start_binding(first["id"]))
    await asyncio.wait_for(start_entered.wait(), timeout=1.0)
    deleting = await repository.mark_deleting("pa_1", first["id"], owner_user_id="owner-a")
    assert deleting is not None
    release_start.set()

    with pytest.raises(BindingCleanupPendingError):
        await start_task
    assert supervisor.running_binding_ids == ()
    assert factory.instances[-1].is_running is False


@pytest.mark.asyncio
async def test_start_claim_rejects_delete_committed_after_final_row_read(
    supervisor_env: tuple[AgentChannelRepository, LocalEncryptedSecretStore, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set())
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    final_read_returned = asyncio.Event()
    release_final_read = asyncio.Event()
    original_binding = supervisor._binding

    async def pause_after_final_read(binding_id: str) -> dict[str, Any]:
        row = await original_binding(binding_id)
        if factory.instances and not final_read_returned.is_set():
            final_read_returned.set()
            await release_final_read.wait()
        return row

    monkeypatch.setattr(supervisor, "_binding", pause_after_final_read)
    start_task = asyncio.create_task(supervisor.start_binding(first["id"]))
    await asyncio.wait_for(final_read_returned.wait(), timeout=1.0)
    deleting = await repository.mark_deleting("pa_1", first["id"], owner_user_id="owner-a")
    assert deleting is not None
    release_final_read.set()

    with pytest.raises(BindingCleanupPendingError):
        await start_task
    assert supervisor.running_binding_ids == ()
    assert factory.instances[-1].is_running is False


@pytest.mark.asyncio
async def test_start_claim_revoked_before_registration_never_publishes_runtime(
    supervisor_env: tuple[AgentChannelRepository, LocalEncryptedSecretStore, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set())

    class _Registry:
        def __init__(self) -> None:
            self.registered: set[str] = set()

        def register_dynamic_channel(self, channel: Channel) -> None:
            self.registered.add(channel.name)

        def unregister_dynamic_channel(self, channel_name: str) -> None:
            self.registered.discard(channel_name)

    registry = _Registry()
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_LEASE_TTL_SECONDS", 0.1)
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_LEASE_RELEASE_WAIT_SECONDS", 1.0)
    starter = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=factory,
        channel_registry=registry,
    )
    deleter = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    claim_committed = asyncio.Event()
    release_claim = asyncio.Event()
    original_claim = repository.claim_runtime

    async def pause_after_claim(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        claimed = await original_claim(*args, **kwargs)
        if claimed is not None:
            claim_committed.set()
            await release_claim.wait()
        return claimed

    monkeypatch.setattr(repository, "claim_runtime", pause_after_claim)
    start_task = asyncio.create_task(starter.start_binding(first["id"]))
    await asyncio.wait_for(claim_committed.wait(), timeout=1.0)
    delete_task = asyncio.create_task(deleter.delete_binding("pa_1", first["id"], owner_user_id="owner-a"))
    await asyncio.sleep(0.05)
    assert not delete_task.done()
    release_claim.set()
    deleted = await asyncio.wait_for(delete_task, timeout=1.0)
    assert deleted["id"] == first["id"]

    with pytest.raises((BindingNotFoundError, BindingCleanupPendingError, BindingStartError)):
        await start_task
    assert starter.running_binding_ids == ()
    assert registry.registered == set()
    assert factory.instances[-1].is_running is False


@pytest.mark.asyncio
async def test_delete_holds_binding_lifecycle_until_row_is_gone(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set())
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    delete_entered = asyncio.Event()
    release_delete = asyncio.Event()
    original_delete = repository.delete

    async def blocked_delete(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        delete_entered.set()
        await release_delete.wait()
        return await original_delete(*args, **kwargs)

    monkeypatch.setattr(repository, "delete", blocked_delete)
    delete_task = asyncio.create_task(
        supervisor.delete_binding(
            "pa_1",
            first["id"],
            owner_user_id="owner-a",
        )
    )
    await asyncio.wait_for(delete_entered.wait(), timeout=1.0)
    concurrent_start = asyncio.create_task(supervisor.start_binding(first["id"]))
    await asyncio.sleep(0)

    assert not concurrent_start.done()
    release_delete.set()
    deleted = await delete_task
    with pytest.raises(BindingNotFoundError):
        await concurrent_start

    assert deleted is not None
    assert deleted["secret_ref"] == first["secret_ref"]
    assert supervisor.running_binding_ids == ()
    assert await repository.get("pa_1", first["id"], owner_user_id="owner-a") is None


@pytest.mark.asyncio
async def test_deleted_binding_lock_is_reclaimed_after_concurrent_waiters_exit(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    delete_entered = asyncio.Event()
    release_delete = asyncio.Event()
    original_delete = repository.delete

    async def blocked_delete(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        delete_entered.set()
        await release_delete.wait()
        return await original_delete(*args, **kwargs)

    monkeypatch.setattr(repository, "delete", blocked_delete)
    delete_task = asyncio.create_task(
        supervisor.delete_binding(
            "pa_1",
            first["id"],
            owner_user_id="owner-a",
        )
    )
    await asyncio.wait_for(delete_entered.wait(), timeout=1.0)
    waiters = [asyncio.create_task(supervisor.start_binding(first["id"])) for _ in range(20)]
    await asyncio.sleep(0)
    assert len(supervisor._binding_locks) == 1

    release_delete.set()
    await delete_task
    results = await asyncio.gather(*waiters, return_exceptions=True)

    assert all(isinstance(result, BindingNotFoundError) for result in results)
    assert supervisor._binding_locks == {}


@pytest.mark.asyncio
async def test_delete_serializes_concurrent_credential_rotation(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    delete_entered = asyncio.Event()
    release_delete = asyncio.Event()
    original_delete = repository.delete

    async def blocked_delete(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        delete_entered.set()
        await release_delete.wait()
        return await original_delete(*args, **kwargs)

    monkeypatch.setattr(repository, "delete", blocked_delete)
    delete_task = asyncio.create_task(
        supervisor.delete_binding(
            "pa_1",
            first["id"],
            owner_user_id="owner-a",
        )
    )
    await asyncio.wait_for(delete_entered.wait(), timeout=1.0)
    rotation_task = asyncio.create_task(
        supervisor.rotate_binding_credentials(
            "pa_1",
            first["id"],
            owner_user_id="owner-a",
            app_id="cli-rotated-after-delete",
            secret_ref="secret-after-delete",
        )
    )
    await asyncio.sleep(0)
    assert not rotation_task.done()

    release_delete.set()
    await delete_task
    with pytest.raises(BindingNotFoundError):
        await rotation_task

    assert supervisor.running_binding_ids == ()


@pytest.mark.asyncio
async def test_startup_list_snapshot_cannot_restart_a_deleted_binding(
    supervisor_env: SupervisorEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, secrets, first, _second = supervisor_env
    await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    listed = asyncio.Event()
    release_list = asyncio.Event()
    original_list_active = repository.list_active

    async def stale_list_active(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        rows = await original_list_active(*args, **kwargs)
        listed.set()
        await release_list.wait()
        return rows

    monkeypatch.setattr(repository, "list_active", stale_list_active)
    startup = asyncio.create_task(supervisor.load_active_bindings())
    await asyncio.wait_for(listed.wait(), timeout=1.0)
    await supervisor.delete_binding(
        "pa_1",
        first["id"],
        owner_user_id="owner-a",
    )
    release_list.set()
    await startup

    assert supervisor.running_binding_ids == ()
    await supervisor.shutdown()
