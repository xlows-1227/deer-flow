from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.channels.base import Channel
from app.channels.message_bus import MessageBus, OutboundMessage
from app.channels.supervisor import BindingNotFoundError, FeishuSupervisor
from app.gateway.routers import published_agent_channels
from deerflow.config.paths import Paths
from deerflow.persistence.agent_channel import AgentChannelRepository
from deerflow.persistence.agent_channel.model import AgentChannelRow
from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import PublishedAgentRow
from deerflow.publishing.feishu_credentials import decode_feishu_credentials
from deerflow.publishing.secret_store import LocalEncryptedSecretStore


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
    ) -> None:
        super().__init__(name=f"feishu:{binding_id}", bus=bus, config={})
        self.app_id = app_id
        self.app_secret = app_secret
        self.verification_token = verification_token
        self.encrypt_key = encrypt_key
        self.agent_id = agent_id
        self.runtime_error_callback = runtime_error_callback
        self.runtime_health_callback = runtime_health_callback
        self.fail_start = fail_start

    async def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("connection refused")
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        return None


class _Factory:
    def __init__(self, fail_app_ids: set[str] | None = None) -> None:
        self.instances: list[_FakeFeishuChannel] = []
        self.fail_app_ids = fail_app_ids or set()

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
        )
        self.instances.append(channel)
        return channel


class _DisposableAsyncEngine(Protocol):
    async def dispose(self) -> None: ...


class _ShutdownableSupervisor(Protocol):
    async def shutdown(self) -> None: ...


class _RouterTestResources:
    """Own one Router test's async resources from creation through teardown."""

    def __init__(self) -> None:
        self.engine: _DisposableAsyncEngine | None = None
        self.supervisor: _ShutdownableSupervisor | None = None

    def own_engine(self, engine: AsyncEngine) -> AsyncEngine:
        self.engine = engine
        return engine

    def own_supervisor(self, supervisor: FeishuSupervisor) -> FeishuSupervisor:
        self.supervisor = supervisor
        return supervisor

    async def close(self) -> None:
        if self.engine is None:
            return
        if self.supervisor is None:
            await self.engine.dispose()
            return
        shutdown_error: BaseException | None = None
        try:
            await self.supervisor.shutdown()
        except BaseException as exc:
            shutdown_error = exc
        try:
            await self.engine.dispose()
        except BaseException as dispose_error:
            if shutdown_error is None:
                raise
            raise BaseExceptionGroup(
                "Router test resource cleanup failed",
                [shutdown_error, dispose_error],
            )
        if shutdown_error is not None:
            raise shutdown_error


@pytest_asyncio.fixture
async def router_test_resources() -> AsyncIterator[_RouterTestResources]:
    """Dispose the DB even when assertions or Supervisor shutdown fail."""
    resources = _RouterTestResources()
    try:
        yield resources
    finally:
        await resources.close()


@pytest.mark.asyncio
async def test_router_resource_cleanup_disposes_engine_when_supervisor_shutdown_fails() -> None:
    events: list[str] = []

    class FailingSupervisor:
        async def shutdown(self) -> None:
            events.append("shutdown")
            raise RuntimeError("runtime ownership unresolved")

    class RecordingEngine:
        async def dispose(self) -> None:
            events.append("dispose")

    resources = _RouterTestResources()
    resources.supervisor = FailingSupervisor()
    resources.engine = RecordingEngine()

    with pytest.raises(RuntimeError, match="runtime ownership unresolved"):
        await resources.close()

    assert events == ["shutdown", "dispose"]


@pytest.mark.asyncio
async def test_router_resource_cleanup_preserves_shutdown_and_dispose_failures() -> None:
    class FailingSupervisor:
        async def shutdown(self) -> None:
            raise RuntimeError("runtime ownership unresolved")

    class FailingEngine:
        async def dispose(self) -> None:
            raise OSError("database disposal failed")

    resources = _RouterTestResources()
    resources.supervisor = FailingSupervisor()
    resources.engine = FailingEngine()

    with pytest.raises(ExceptionGroup) as captured:
        await resources.close()

    failures = captured.value.exceptions
    assert len(failures) == 2
    assert isinstance(failures[0], RuntimeError)
    assert str(failures[0]) == "runtime ownership unresolved"
    assert isinstance(failures[1], OSError)
    assert str(failures[1]) == "database disposal failed"


@pytest.mark.asyncio
async def test_list_channels_preserves_deleting_status_contract(
    tmp_path: Path,
    router_test_resources: _RouterTestResources,
) -> None:
    engine = router_test_resources.own_engine(create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'deleting-contract.db'}"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            PublishedAgentRow(
                id="pa_1",
                owner_user_id="owner-a",
                slug="one",
                display_name="One",
                status="published",
            )
        )
        session.add(
            AgentChannelRow(
                id="ach_deleting",
                agent_id="pa_1",
                app_id="cli_deleting",
                secret_ref="secret://redacted",
                status="deleting",
                delete_previous_status="active",
            )
        )
        await session.commit()

    app = FastAPI()
    app.state.agent_channel_repo = AgentChannelRepository(session_factory)

    @app.middleware("http")
    async def session_auth(request: Request, call_next: Any) -> Any:
        request.state.user = SimpleNamespace(id="owner-a")
        request.state.auth_method = "session"
        return await call_next(request)

    app.include_router(published_agent_channels.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/published-agents/pa_1/channels")

    assert response.status_code == 200
    assert response.json()[0]["status"] == "deleting"
    assert "secret_ref" not in response.json()[0]


@pytest.fixture(scope="module", autouse=True)
def stop_process_scanner_after_router_module() -> Iterator[None]:
    """Keep the process-owned scanner inside this module's test lifetime."""
    yield
    from app.channels.feishu import stop_published_attachment_backlog_scanner

    stop_published_attachment_backlog_scanner()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["post", "patch"])
async def test_owner_write_heartbeat_blocks_janitor_during_slow_secret_write(
    operation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / f'writer-{operation}.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(PublishedAgentRow(id="pa_1", owner_user_id="owner-a", slug="one", display_name="One", status="published"))
        await session.commit()
    repository = AgentChannelRepository(session_factory)
    secrets = LocalEncryptedSecretStore(tmp_path / "secrets", key=Fernet.generate_key())
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory())
    app = FastAPI()
    app.state.agent_channel_repo = repository
    app.state.agent_channel_secret_store = secrets
    app.state.feishu_supervisor = supervisor

    @app.middleware("http")
    async def session_auth(request: Request, call_next: Any) -> Any:
        request.state.user = SimpleNamespace(id="owner-a")
        request.state.auth_method = "session"
        return await call_next(request)

    app.include_router(published_agent_channels.router)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            binding_id: str | None = None
            if operation == "patch":
                initial = await client.post(
                    "/api/published-agents/pa_1/channels",
                    json={
                        "app_id": "cli-initial",
                        "app_secret": "initial-secret",
                        "verification_token": "initial-token",
                    },
                )
                assert initial.status_code == 201
                binding_id = initial.json()["id"]

            original_reserve = repository.reserve_secret_ingest

            async def reserve_due_now(**kwargs: Any) -> dict[str, Any] | None:
                return await original_reserve(**kwargs, defer_seconds=0)

            original_put = secrets.put_reserved
            write_entered = asyncio.Event()
            release_write = asyncio.Event()

            async def slow_put(secret_ref: str, secret: str) -> None:
                write_entered.set()
                await release_write.wait()
                await original_put(secret_ref, secret)

            monkeypatch.setattr(repository, "reserve_secret_ingest", reserve_due_now)
            monkeypatch.setattr(secrets, "put_reserved", slow_put)
            # The assertion is event-driven; the lease only needs enough
            # margin that a loaded Windows runner cannot expire it before the
            # first scheduled heartbeat executes.
            monkeypatch.setattr(published_agent_channels, "SECRET_INGEST_WRITER_LEASE_SECONDS", 3.0)
            original_renew = repository.renew_secret_ingest_write
            writer_renewed = asyncio.Event()

            async def observe_renew(*args: Any, **kwargs: Any) -> bool:
                renewed = await original_renew(*args, **kwargs)
                if renewed:
                    writer_renewed.set()
                return renewed

            monkeypatch.setattr(repository, "renew_secret_ingest_write", observe_renew)
            if operation == "post":
                request_task = asyncio.create_task(
                    client.post(
                        "/api/published-agents/pa_1/channels",
                        json={
                            "app_id": "cli-slow-post",
                            "app_secret": "slow-post-secret",
                            "verification_token": "slow-post-token",
                        },
                    )
                )
            else:
                request_task = asyncio.create_task(
                    client.patch(
                        f"/api/published-agents/pa_1/channels/{binding_id}",
                        json={
                            "app_id": "cli-slow-patch",
                            "app_secret": "slow-patch-secret",
                            "verification_token": "slow-patch-token",
                        },
                    )
                )
            try:
                await asyncio.wait_for(write_entered.wait(), timeout=5.0)
                await asyncio.wait_for(writer_renewed.wait(), timeout=5.0)
                release_write.set()
                response = await asyncio.wait_for(request_task, timeout=10.0)
            finally:
                release_write.set()
                if not request_task.done():
                    done, _pending = await asyncio.wait({request_task}, timeout=10.0)
                    if request_task not in done:
                        request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)

        assert response.status_code in {200, 201}
        rows = await repository.list_by_agent("pa_1", owner_user_id="owner-a")
        assert len(rows) == 1
        expected_secret = "slow-post-secret" if operation == "post" else "slow-patch-secret"
        assert decode_feishu_credentials(await secrets.get(rows[0]["secret_ref"])).app_secret == expected_secret
    finally:
        try:
            await supervisor.shutdown()
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_owner_channel_api_never_returns_secret_and_supports_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    router_test_resources: _RouterTestResources,
) -> None:
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path / "runtime"))
    engine = router_test_resources.own_engine(create_async_engine("sqlite+aiosqlite:///:memory:"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(PublishedAgentRow(id="pa_1", owner_user_id="owner-a", slug="one", display_name="One", status="published"))
        await session.commit()
    repository = AgentChannelRepository(session_factory)
    secrets = LocalEncryptedSecretStore(tmp_path / "secrets", key=Fernet.generate_key())
    factory = _Factory()
    supervisor = router_test_resources.own_supervisor(FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory))
    app = FastAPI()
    app.state.agent_channel_repo = repository
    app.state.agent_channel_secret_store = secrets
    app.state.feishu_supervisor = supervisor

    @app.middleware("http")
    async def session_auth(request: Request, call_next: Any) -> Any:
        request.state.user = SimpleNamespace(id=request.headers.get("X-Test-User", "owner-a"))
        request.state.auth_method = "session"
        return await call_next(request)

    app.include_router(published_agent_channels.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/published-agents/pa_1/channels",
            json={
                "app_id": "cli_owner",
                "app_secret": "owner-secret-value",
                "verification_token": "owner-verification-token",
                "encrypt_key": "owner-encrypt-key",
            },
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["secret_configured"] is True
        assert "owner-secret-value" not in json.dumps(payload)
        assert "secret_ref" not in payload
        binding_id = payload["id"]
        stored = await repository.get("pa_1", binding_id, owner_user_id="owner-a")
        credentials = decode_feishu_credentials(await secrets.get(stored["secret_ref"]))
        assert credentials.app_secret == "owner-secret-value"
        assert credentials.verification_token == "owner-verification-token"
        assert credentials.encrypt_key == "owner-encrypt-key"

        cross_owner = await client.get(
            "/api/published-agents/pa_1/channels",
            headers={"X-Test-User": "owner-b"},
        )
        assert cross_owner.status_code == 404

        started = await client.post(f"/api/published-agents/pa_1/channels/{binding_id}/start")
        assert started.status_code == 200
        assert started.json()["status"] == "active"
        assert started.json()["health"] == "healthy"

        original_secret_delete = secrets.delete
        superseded_delete_failed = False

        async def fail_superseded_cleanup_once(secret_ref: str) -> bool:
            nonlocal superseded_delete_failed
            if not superseded_delete_failed:
                superseded_delete_failed = True
                raise RuntimeError("secret store unavailable")
            return await original_secret_delete(secret_ref)

        monkeypatch.setattr(secrets, "delete", fail_superseded_cleanup_once)
        rotated = await client.patch(
            f"/api/published-agents/pa_1/channels/{binding_id}",
            json={
                "app_id": "cli_rotated",
                "app_secret": "rotated-secret-value",
                "verification_token": "rotated-verification-token",
                "encrypt_key": "rotated-encrypt-key",
            },
        )
        assert rotated.status_code == 200
        assert "rotated-secret-value" not in json.dumps(rotated.json())
        assert factory.instances[-1].app_secret == "rotated-secret-value"
        assert factory.instances[-1].verification_token == "rotated-verification-token"
        assert factory.instances[-1].encrypt_key == "rotated-encrypt-key"
        retained_after_success = await repository.get("pa_1", binding_id, owner_user_id="owner-a")
        assert retained_after_success is not None
        assert retained_after_success["secret_cleanup_ref"] is not None
        assert retained_after_success["secret_cleanup_reason"] == "rotation_superseded"
        assert len(list((tmp_path / "secrets").rglob("*.secret"))) == 2
        monkeypatch.setattr(secrets, "delete", original_secret_delete)
        assert await supervisor.cleanup_binding_secrets(binding_id) is True
        assert len(list((tmp_path / "secrets").rglob("*.secret"))) == 1

        retained_before_failure = await repository.get("pa_1", binding_id, owner_user_id="owner-a")
        factory.fail_app_ids.add("cli_broken")
        delete_failed = False

        async def fail_rotation_cleanup_once(secret_ref: str) -> bool:
            nonlocal delete_failed
            if not delete_failed:
                delete_failed = True
                raise RuntimeError("secret store unavailable")
            return await original_secret_delete(secret_ref)

        monkeypatch.setattr(secrets, "delete", fail_rotation_cleanup_once)
        failed_rotation = await client.patch(
            f"/api/published-agents/pa_1/channels/{binding_id}",
            json={
                "app_id": "cli_broken",
                "app_secret": "broken-secret-value",
                "verification_token": "broken-verification-token",
                "encrypt_key": "broken-encrypt-key",
            },
        )
        assert failed_rotation.status_code == 502
        retained_after_failure = await repository.get("pa_1", binding_id, owner_user_id="owner-a")
        assert retained_after_failure is not None
        assert retained_after_failure["app_id"] == retained_before_failure["app_id"]
        assert retained_after_failure["secret_ref"] == retained_before_failure["secret_ref"]
        assert factory.instances[-1].app_id == "cli_rotated"
        assert factory.instances[-1].is_running is True
        assert await secrets.get(retained_after_failure["secret_ref"])
        assert retained_after_failure["secret_cleanup_ref"] is not None
        assert retained_after_failure["secret_cleanup_reason"] == "rotation_rollback"
        assert len(list((tmp_path / "secrets").rglob("*.secret"))) == 2
        monkeypatch.setattr(secrets, "delete", original_secret_delete)
        assert await supervisor.cleanup_binding_secrets(binding_id) is True
        recovered = await repository.get("pa_1", binding_id, owner_user_id="owner-a")
        assert recovered is not None
        assert recovered["secret_cleanup_ref"] is None
        assert len(list((tmp_path / "secrets").rglob("*.secret"))) == 1

        stopped = await client.post(f"/api/published-agents/pa_1/channels/{binding_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "inactive"

        oversized_path = tmp_path / "runtime" / "published-attachment-cleanup" / "oversized-delete.json"
        oversized_path.parent.mkdir(parents=True, exist_ok=True)
        oversized_path.write_text("x" * (1024 * 1024 + 1), encoding="utf-8")
        oversized_delete = await client.delete(f"/api/published-agents/pa_1/channels/{binding_id}")
        assert oversized_delete.status_code == 409
        assert await repository.get("pa_1", binding_id, owner_user_id="owner-a") is not None
        oversized_path.unlink()

        outbox_path = tmp_path / "runtime" / "published-attachment-cleanup" / "pending-delete.json"
        outbox_path.parent.mkdir(parents=True, exist_ok=True)
        outbox_path.write_text(
            json.dumps(
                {
                    "job_id": "pending-delete",
                    "binding_id": binding_id,
                    "thread_id": "thread-pending-delete",
                    "owner_user_id": "owner-a",
                    "virtual_paths": ["/mnt/user-data/uploads/input.bin"],
                    "phase": "ready_to_delete",
                    "producer_token": None,
                    "producer_lease_expires_at": None,
                    "claim_token": None,
                    "claim_lease_expires_at": None,
                    "version": 1,
                }
            ),
            encoding="utf-8",
        )
        blocked_delete = await client.delete(f"/api/published-agents/pa_1/channels/{binding_id}")
        assert blocked_delete.status_code == 409
        retained = await repository.get("pa_1", binding_id, owner_user_id="owner-a")
        assert retained is not None
        assert await secrets.get(retained["secret_ref"]) is not None

        outbox_path.unlink()
        deleted = await client.delete(f"/api/published-agents/pa_1/channels/{binding_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True}
        assert await repository.get("pa_1", binding_id, owner_user_id="owner-a") is None


@pytest.mark.asyncio
async def test_connection_test_returns_health_without_echoing_secret(
    tmp_path: Path,
    router_test_resources: _RouterTestResources,
) -> None:
    engine = router_test_resources.own_engine(create_async_engine("sqlite+aiosqlite:///:memory:"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(PublishedAgentRow(id="pa_1", owner_user_id="owner-a", slug="one", display_name="One", status="published"))
        await session.commit()
    repository = AgentChannelRepository(session_factory)
    secrets = LocalEncryptedSecretStore(tmp_path / "secrets", key=Fernet.generate_key())

    async def connection_tester(app_id: str, app_secret: str) -> tuple[bool, str]:
        assert app_id == "cli_owner"
        assert app_secret == "owner-secret-value"
        return True, "connected"

    supervisor = router_test_resources.own_supervisor(
        FeishuSupervisor(
            repository,
            secrets,
            MessageBus(),
            channel_factory=_Factory(),
            connection_tester=connection_tester,
        )
    )
    app = FastAPI()
    app.state.agent_channel_repo = repository
    app.state.agent_channel_secret_store = secrets
    app.state.feishu_supervisor = supervisor

    @app.middleware("http")
    async def session_auth(request: Request, call_next: Any) -> Any:
        request.state.user = SimpleNamespace(id="owner-a")
        request.state.auth_method = "session"
        return await call_next(request)

    app.include_router(published_agent_channels.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/published-agents/pa_1/channels",
            json={
                "app_id": "cli_owner",
                "app_secret": "owner-secret-value",
                "verification_token": "owner-verification-token",
                "encrypt_key": "owner-encrypt-key",
            },
        )
        binding_id = created.json()["id"]
        tested = await client.post(f"/api/published-agents/pa_1/channels/{binding_id}/test")

    assert tested.status_code == 200
    assert tested.json() == {"health": "healthy", "detail": "connected"}
    assert "owner-secret-value" not in tested.text


@pytest.mark.asyncio
async def test_post_crash_after_ciphertext_write_is_recovered_from_database_ingest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    router_test_resources: _RouterTestResources,
) -> None:
    engine = router_test_resources.own_engine(create_async_engine("sqlite+aiosqlite:///:memory:"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(PublishedAgentRow(id="pa_1", owner_user_id="owner-a", slug="one", display_name="One", status="published"))
        await session.commit()
    repository = AgentChannelRepository(session_factory)
    secrets = LocalEncryptedSecretStore(tmp_path / "secrets", key=Fernet.generate_key())
    supervisor = router_test_resources.own_supervisor(FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory()))
    original_reserve = repository.reserve_secret_ingest

    async def reserve_due_now(**kwargs: Any) -> dict[str, Any] | None:
        return await original_reserve(**kwargs, defer_seconds=0)

    async def crash_before_binding_commit(**_kwargs: Any) -> dict[str, Any] | None:
        raise RuntimeError("simulated process exit before binding commit")

    async def skip_in_process_compensation(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(repository, "reserve_secret_ingest", reserve_due_now)
    monkeypatch.setattr(repository, "create_from_secret_ingest", crash_before_binding_commit)
    monkeypatch.setattr(published_agent_channels, "_discard_unstaged_secret", skip_in_process_compensation)
    app = FastAPI()
    app.state.agent_channel_repo = repository
    app.state.agent_channel_secret_store = secrets
    app.state.feishu_supervisor = supervisor

    @app.middleware("http")
    async def session_auth(request: Request, call_next: Any) -> Any:
        request.state.user = SimpleNamespace(id="owner-a")
        request.state.auth_method = "session"
        return await call_next(request)

    app.include_router(published_agent_channels.router)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="simulated process exit"):
            await client.post(
                "/api/published-agents/pa_1/channels",
                json={
                    "app_id": "cli-crash",
                    "app_secret": "crash-secret",
                    "verification_token": "crash-token",
                    "encrypt_key": "",
                },
            )

    assert len(list((tmp_path / "secrets").rglob("*.secret"))) == 1
    assert await repository.list_by_agent("pa_1", owner_user_id="owner-a") == []
    await supervisor.recover_cleanup_state()
    assert list((tmp_path / "secrets").rglob("*.secret")) == []


@pytest.mark.asyncio
async def test_post_commit_wins_before_route_failure_and_keeps_current_ciphertext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    router_test_resources: _RouterTestResources,
) -> None:
    engine = router_test_resources.own_engine(create_async_engine("sqlite+aiosqlite:///:memory:"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(PublishedAgentRow(id="pa_1", owner_user_id="owner-a", slug="one", display_name="One", status="published"))
        await session.commit()
    repository = AgentChannelRepository(session_factory)
    secrets = LocalEncryptedSecretStore(tmp_path / "secrets", key=Fernet.generate_key())
    supervisor = router_test_resources.own_supervisor(FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory()))
    original_create = repository.create_from_secret_ingest

    async def commit_then_exit(**kwargs: Any) -> dict[str, Any] | None:
        created = await original_create(**kwargs)
        assert created is not None
        raise RuntimeError("simulated process exit after binding commit")

    monkeypatch.setattr(repository, "create_from_secret_ingest", commit_then_exit)
    app = FastAPI()
    app.state.agent_channel_repo = repository
    app.state.agent_channel_secret_store = secrets
    app.state.feishu_supervisor = supervisor

    @app.middleware("http")
    async def session_auth(request: Request, call_next: Any) -> Any:
        request.state.user = SimpleNamespace(id="owner-a")
        request.state.auth_method = "session"
        return await call_next(request)

    app.include_router(published_agent_channels.router)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="simulated process exit"):
            await client.post(
                "/api/published-agents/pa_1/channels",
                json={
                    "app_id": "cli-committed",
                    "app_secret": "committed-secret",
                    "verification_token": "committed-token",
                    "encrypt_key": "",
                },
            )

    rows = await repository.list_by_agent("pa_1", owner_user_id="owner-a")
    assert len(rows) == 1
    assert decode_feishu_credentials(await secrets.get(rows[0]["secret_ref"])).app_secret == "committed-secret"
    await supervisor.recover_cleanup_state()
    assert await secrets.get(rows[0]["secret_ref"])


@pytest.mark.asyncio
async def test_patch_recovers_crashed_rotation_candidate_before_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    router_test_resources: _RouterTestResources,
) -> None:
    engine = router_test_resources.own_engine(create_async_engine("sqlite+aiosqlite:///:memory:"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(PublishedAgentRow(id="pa_1", owner_user_id="owner-a", slug="one", display_name="One", status="published"))
        await session.commit()
    repository = AgentChannelRepository(session_factory)
    secrets = LocalEncryptedSecretStore(tmp_path / "secrets", key=Fernet.generate_key())
    old_ref = await secrets.put('{"app_secret":"old","verification_token":"old-token","encrypt_key":""}')
    candidate_ref = await secrets.put('{"app_secret":"candidate","verification_token":"candidate-token","encrypt_key":""}')
    row = await repository.create(
        agent_id="pa_1",
        owner_user_id="owner-a",
        app_id="cli-old",
        secret_ref=old_ref,
    )
    assert row is not None
    await repository.stage_secret_cleanup(
        "pa_1",
        row["id"],
        owner_user_id="owner-a",
        secret_ref=candidate_ref,
        defer_seconds=0,
    )
    await repository.update_credentials(
        "pa_1",
        row["id"],
        owner_user_id="owner-a",
        app_id="cli-candidate",
        secret_ref=candidate_ref,
    )
    supervisor = router_test_resources.own_supervisor(FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory()))

    async def binding_disappeared(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise BindingNotFoundError(row["id"])

    monkeypatch.setattr(supervisor, "rotate_binding_credentials", binding_disappeared)
    app = FastAPI()
    app.state.agent_channel_repo = repository
    app.state.agent_channel_secret_store = secrets
    app.state.feishu_supervisor = supervisor

    @app.middleware("http")
    async def session_auth(request: Request, call_next: Any) -> Any:
        request.state.user = SimpleNamespace(id="owner-a")
        request.state.auth_method = "session"
        return await call_next(request)

    app.include_router(published_agent_channels.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/api/published-agents/pa_1/channels/{row['id']}",
            json={
                "app_id": "cli-next",
                "app_secret": "next-secret",
                "verification_token": "next-token",
                "encrypt_key": "",
            },
        )

    assert response.status_code == 404
    current = await repository.get("pa_1", row["id"], owner_user_id="owner-a")
    assert current is not None
    assert current["secret_ref"] == candidate_ref
    assert await secrets.get(candidate_ref)
    with pytest.raises(KeyError):
        await secrets.get(old_ref)


@pytest.mark.asyncio
async def test_patch_deleting_race_returns_conflict_and_erases_unstaged_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    router_test_resources: _RouterTestResources,
) -> None:
    engine = router_test_resources.own_engine(create_async_engine("sqlite+aiosqlite:///:memory:"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(PublishedAgentRow(id="pa_1", owner_user_id="owner-a", slug="one", display_name="One", status="published"))
        await session.commit()
    repository = AgentChannelRepository(session_factory)
    secrets = LocalEncryptedSecretStore(tmp_path / "secrets", key=Fernet.generate_key())
    old_ref = await secrets.put('{"app_secret":"old","verification_token":"old-token","encrypt_key":""}')
    row = await repository.create(agent_id="pa_1", owner_user_id="owner-a", app_id="cli-old", secret_ref=old_ref)
    assert row is not None
    supervisor = router_test_resources.own_supervisor(FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory()))
    original_put_reserved = secrets.put_reserved
    created_refs: list[str] = []

    async def put_then_delete(secret_ref: str, secret: str) -> None:
        await original_put_reserved(secret_ref, secret)
        created_refs.append(secret_ref)
        marked = await repository.mark_deleting("pa_1", row["id"], owner_user_id="owner-a")
        assert marked is not None

    monkeypatch.setattr(secrets, "put_reserved", put_then_delete)
    app = FastAPI()
    app.state.agent_channel_repo = repository
    app.state.agent_channel_secret_store = secrets
    app.state.feishu_supervisor = supervisor

    @app.middleware("http")
    async def session_auth(request: Request, call_next: Any) -> Any:
        request.state.user = SimpleNamespace(id="owner-a")
        request.state.auth_method = "session"
        return await call_next(request)

    app.include_router(published_agent_channels.router)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/api/published-agents/pa_1/channels/{row['id']}",
            json={
                "app_id": "cli-new",
                "app_secret": "new-secret",
                "verification_token": "new-token",
                "encrypt_key": "",
            },
        )

    assert response.status_code == 409
    assert len(created_refs) == 1
    with pytest.raises(KeyError):
        await secrets.get(created_refs[0])
