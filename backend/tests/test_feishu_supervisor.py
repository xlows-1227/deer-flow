from __future__ import annotations

from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channels.base import Channel
from app.channels.message_bus import MessageBus, OutboundMessage
from app.channels.supervisor import FeishuSupervisor
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
        fail_start=False,
        fail_stop=False,
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
            fail_start=app_id in self.fail_app_ids,
            fail_stop=app_id in self.fail_stop_app_ids,
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
