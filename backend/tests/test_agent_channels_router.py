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
from deerflow.persistence.agent_channel import AgentChannelRepository
from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import PublishedAgentRow
from deerflow.publishing.secret_store import LocalEncryptedSecretStore


class _FakeFeishuChannel(Channel):
    def __init__(self, bus, *, app_id, app_secret, binding_id, agent_id) -> None:
        super().__init__(name=f"feishu:{binding_id}", bus=bus, config={})
        self.app_id = app_id
        self.app_secret = app_secret
        self.agent_id = agent_id

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        return None


class _Factory:
    def __init__(self) -> None:
        self.instances: list[_FakeFeishuChannel] = []

    def __call__(self, bus, *, app_id, app_secret, binding_id, agent_id):
        channel = _FakeFeishuChannel(
            bus,
            app_id=app_id,
            app_secret=app_secret,
            binding_id=binding_id,
            agent_id=agent_id,
        )
        self.instances.append(channel)
        return channel


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_owner_channel_api_never_returns_secret_and_supports_lifecycle(tmp_path) -> None:
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
            json={"app_id": "cli_owner", "app_secret": "owner-secret-value"},
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["secret_configured"] is True
        assert "owner-secret-value" not in json.dumps(payload)
        assert "secret_ref" not in payload
        binding_id = payload["id"]

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
            json={"app_id": "cli_rotated", "app_secret": "rotated-secret-value"},
        )
        assert rotated.status_code == 200
        assert "rotated-secret-value" not in json.dumps(rotated.json())
        assert factory.instances[-1].app_secret == "rotated-secret-value"

        stopped = await client.post(f"/api/published-agents/pa_1/channels/{binding_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "inactive"

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
            json={"app_id": "cli_owner", "app_secret": "owner-secret-value"},
        )
        binding_id = created.json()["id"]
        tested = await client.post(f"/api/published-agents/pa_1/channels/{binding_id}/test")

    assert tested.status_code == 200
    assert tested.json() == {"health": "healthy", "detail": "connected"}
    assert "owner-secret-value" not in tested.text
    await engine.dispose()
