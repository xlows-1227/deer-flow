from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channels.base import Channel
from app.channels.message_bus import MessageBus, OutboundMessage
from app.channels.supervisor import BindingNotFoundError, FeishuSupervisor
from deerflow.config.paths import Paths, get_paths
from deerflow.persistence.agent_channel import AgentChannelRepository
from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import PublishedAgentRow
from deerflow.publishing.feishu_credentials import FeishuCredentials, encode_feishu_credentials
from deerflow.publishing.secret_store import LocalEncryptedSecretStore


class _FakeFeishuChannel(Channel):
    def __init__(
        self,
        bus,
        *,
        app_id,
        app_secret,
        verification_token,
        encrypt_key,
        binding_id,
        agent_id,
        runtime_error_callback,
        runtime_health_callback=None,
        fail_start=False,
        fail_stop=False,
        attachment_cleanup_healthy=True,
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
            raise RuntimeError("connection did not close")
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        return None


@dataclass
class _Factory:
    fail_app_ids: set[str]
    fail_stop_app_ids: set[str] = field(default_factory=set)
    cleanup_unhealthy_app_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.instances: list[_FakeFeishuChannel] = []

    def __call__(
        self,
        bus,
        *,
        app_id,
        app_secret,
        verification_token,
        encrypt_key,
        binding_id,
        agent_id,
        runtime_error_callback,
        runtime_health_callback=None,
    ):
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
            attachment_cleanup_healthy=app_id not in self.cleanup_unhealthy_app_ids,
        )
        self.instances.append(channel)
        return channel


@pytest_asyncio.fixture
async def supervisor_env(tmp_path):
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


@pytest.mark.asyncio
async def test_starting_and_stopping_one_binding_does_not_affect_another(supervisor_env) -> None:
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
async def test_blocked_binding_start_does_not_serialize_peer_lifecycle(supervisor_env) -> None:
    repository, secrets, first, second = supervisor_env
    first_start_entered = asyncio.Event()
    release_first_start = asyncio.Event()
    factory = _Factory(set())

    def blocking_factory(*args, **kwargs):
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
async def test_loading_active_bindings_isolates_start_failures(supervisor_env) -> None:
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


@pytest.mark.asyncio
async def test_gateway_janitor_recovers_cleanup_for_inactive_binding(
    supervisor_env,
    tmp_path,
    monkeypatch,
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

        async def acquire_with_lease_async(self, thread_id: str, *, user_id: str):
            from deerflow.sandbox.sandbox_provider import SandboxAcquisition

            return SandboxAcquisition(
                sandbox_id=f"local:{thread_id}",
                acquisition_token="janitor-acquire",
                thread_id=thread_id,
            )

        def accept_acquisition(self, _acquisition) -> None:
            return None

        def abandon_acquisition(self, _acquisition) -> None:
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
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))

    await supervisor.load_active_bindings()
    try:
        for _ in range(100):
            if not outbox_path.exists():
                break
            await asyncio.sleep(0.01)
        assert not outbox_path.exists()
        assert not host_path.exists()
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_pending_attachment_cleanup_marks_running_binding_unhealthy(supervisor_env) -> None:
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
async def test_runtime_attachment_cleanup_health_updates_without_stopping_binding(supervisor_env) -> None:
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
async def test_restart_rebuilds_channel_with_rotated_secret(supervisor_env) -> None:
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
async def test_runtime_connection_failure_marks_only_that_binding_unhealthy(supervisor_env) -> None:
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
async def test_stale_error_from_replaced_runtime_does_not_poison_new_generation(supervisor_env) -> None:
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
async def test_stop_failure_preserves_active_runtime_and_status(supervisor_env) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set(), {"cli_one"})
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])

    with pytest.raises(RuntimeError, match="did not close"):
        await supervisor.stop_binding(first["id"])

    assert supervisor.running_binding_ids == (first["id"],)
    assert (await repository.get("pa_1", first["id"], owner_user_id="owner-a"))["status"] == "active"


@pytest.mark.asyncio
async def test_shutdown_preserves_desired_active_status_for_next_startup(supervisor_env) -> None:
    repository, secrets, first, _second = supervisor_env
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    await supervisor.start_binding(first["id"])

    await supervisor.shutdown()

    assert supervisor.running_binding_ids == ()
    assert (await repository.get("pa_1", first["id"], owner_user_id="owner-a"))["status"] == "active"


@pytest.mark.asyncio
async def test_delete_holds_binding_lifecycle_until_row_is_gone(supervisor_env, monkeypatch) -> None:
    repository, secrets, first, _second = supervisor_env
    factory = _Factory(set())
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    await supervisor.start_binding(first["id"])
    delete_entered = asyncio.Event()
    release_delete = asyncio.Event()
    original_delete = repository.delete

    async def blocked_delete(*args, **kwargs):
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
async def test_deleted_binding_lock_is_reclaimed_after_concurrent_waiters_exit(supervisor_env, monkeypatch) -> None:
    repository, secrets, first, _second = supervisor_env
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    delete_entered = asyncio.Event()
    release_delete = asyncio.Event()
    original_delete = repository.delete

    async def blocked_delete(*args, **kwargs):
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
async def test_delete_serializes_concurrent_credential_rotation(supervisor_env, monkeypatch) -> None:
    repository, secrets, first, _second = supervisor_env
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    delete_entered = asyncio.Event()
    release_delete = asyncio.Event()
    original_delete = repository.delete

    async def blocked_delete(*args, **kwargs):
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
async def test_startup_list_snapshot_cannot_restart_a_deleted_binding(supervisor_env, monkeypatch) -> None:
    repository, secrets, first, _second = supervisor_env
    await repository.activate("pa_1", first["id"], owner_user_id="owner-a")
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(set()))
    listed = asyncio.Event()
    release_list = asyncio.Event()
    original_list_active = repository.list_active

    async def stale_list_active(*args, **kwargs):
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
