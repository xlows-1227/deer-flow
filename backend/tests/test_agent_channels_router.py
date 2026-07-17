from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channels.base import Channel
from app.channels.message_bus import MessageBus, OutboundMessage
from app.channels.supervisor import FeishuSupervisor
from app.gateway.routers import published_agent_channels
from deerflow.config.paths import Paths
from deerflow.persistence.agent_channel import AgentChannelRepository
from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import PublishedAgentRow
from deerflow.publishing.feishu_credentials import decode_feishu_credentials
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
    ) -> None:
        super().__init__(name=f"feishu:{binding_id}", bus=bus, config={})
        self.app_id = app_id
        self.app_secret = app_secret
        self.verification_token = verification_token
        self.encrypt_key = encrypt_key
        self.agent_id = agent_id
        self.runtime_error_callback = runtime_error_callback
        self.runtime_health_callback = runtime_health_callback

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        return None


class _Factory:
    def __init__(self) -> None:
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
        )
        self.instances.append(channel)
        return channel


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_owner_channel_api_never_returns_secret_and_supports_lifecycle(tmp_path, monkeypatch) -> None:
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path / "runtime"))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(PublishedAgentRow(id="pa_1", owner_user_id="owner-a", slug="one", display_name="One", status="published"))
        await session.commit()
    repository = AgentChannelRepository(session_factory)
    secrets = LocalEncryptedSecretStore(tmp_path / "secrets", key=Fernet.generate_key())
    factory = _Factory()
    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=factory)
    app = FastAPI()
    app.state.agent_channel_repo = repository
    app.state.agent_channel_secret_store = secrets
    app.state.feishu_supervisor = supervisor

    @app.middleware("http")
    async def session_auth(request: Request, call_next):
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

        stopped = await client.post(f"/api/published-agents/pa_1/channels/{binding_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "inactive"

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
    await engine.dispose()


@pytest.mark.anyio
async def test_connection_test_returns_health_without_echoing_secret(tmp_path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(PublishedAgentRow(id="pa_1", owner_user_id="owner-a", slug="one", display_name="One", status="published"))
        await session.commit()
    repository = AgentChannelRepository(session_factory)
    secrets = LocalEncryptedSecretStore(tmp_path / "secrets", key=Fernet.generate_key())

    async def connection_tester(app_id: str, app_secret: str):
        assert app_id == "cli_owner"
        assert app_secret == "owner-secret-value"
        return True, "connected"

    supervisor = FeishuSupervisor(repository, secrets, MessageBus(), channel_factory=_Factory(), connection_tester=connection_tester)
    app = FastAPI()
    app.state.agent_channel_repo = repository
    app.state.agent_channel_secret_store = secrets
    app.state.feishu_supervisor = supervisor

    @app.middleware("http")
    async def session_auth(request: Request, call_next):
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
    await engine.dispose()
