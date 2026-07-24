"""Regression tests for Gateway lifespan shutdown.

These tests guard the invariant that lifespan shutdown is *bounded*: a
misbehaving channel whose ``stop()`` blocks forever must not keep the
uvicorn worker alive. A hung worker is the precondition for the
signal-reentrancy deadlock described in
``app.gateway.app._SHUTDOWN_HOOK_TIMEOUT_SECONDS``.
"""

from __future__ import annotations

import asyncio
import importlib
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channels.message_bus import MessageBus
from app.channels.supervisor import FeishuSupervisor
from deerflow.persistence.agent_channel import AgentChannelRepository
from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import PublishedAgentRow
from deerflow.publishing.feishu_credentials import FeishuCredentials, encode_feishu_credentials
from deerflow.publishing.secret_store import LocalEncryptedSecretStore


@asynccontextmanager
async def _noop_langgraph_runtime(_app, _startup_config):
    yield


async def _run_lifespan_with_hanging_stop() -> float:
    """Drive the lifespan context with stop_channel_service hanging forever.

    Returns the elapsed wall-clock seconds.
    """
    from app.gateway.app import _SHUTDOWN_HOOK_TIMEOUT_SECONDS, lifespan

    async def hang_forever() -> None:
        await asyncio.sleep(3600)

    app = FastAPI()

    fake_service = MagicMock()
    fake_service.get_status = MagicMock(return_value={})

    async def fake_start(_config=None):
        return fake_service

    with (
        patch("app.gateway.app.get_app_config"),
        patch("app.gateway.app.get_gateway_config", return_value=MagicMock(host="x", port=0)),
        patch("app.gateway.app.langgraph_runtime", _noop_langgraph_runtime),
        patch("app.channels.service.start_channel_service", side_effect=fake_start),
        patch("app.channels.service.stop_channel_service", side_effect=hang_forever),
    ):
        loop = asyncio.get_event_loop()
        start = loop.time()
        async with lifespan(app):
            pass
        elapsed = loop.time() - start

    assert _SHUTDOWN_HOOK_TIMEOUT_SECONDS < 30.0, "Timeout constant must stay modest"
    return elapsed


def test_shutdown_is_bounded_when_channel_stop_hangs():
    """Lifespan exit must complete near the configured timeout, not hang."""
    from app.gateway.app import _SHUTDOWN_HOOK_TIMEOUT_SECONDS

    elapsed = asyncio.run(_run_lifespan_with_hanging_stop())

    # Generous upper bound: timeout + 2s slack for scheduling overhead.
    assert elapsed < _SHUTDOWN_HOOK_TIMEOUT_SECONDS + 2.0, f"Lifespan shutdown took {elapsed:.2f}s; expected <= {_SHUTDOWN_HOOK_TIMEOUT_SECONDS + 2.0:.1f}s"
    # Lower bound: the wait_for should actually have waited.
    assert elapsed >= _SHUTDOWN_HOOK_TIMEOUT_SECONDS - 0.5, f"Lifespan exited too quickly ({elapsed:.2f}s); wait_for may not have been invoked."


class _RuntimeLeaderFence:
    def __init__(self) -> None:
        self.held = False

    async def acquire(self) -> bool:
        self.held = True
        return True

    async def release(self) -> None:
        self.held = False


class _NeverStartedChannel:
    is_running = False
    attachment_cleanup_healthy = True

    async def start(self) -> None:
        raise AssertionError("late claim must not start a channel")

    async def stop(self) -> None:
        return None


class _FailingStopChannel:
    def __init__(self, *, binding_id: str) -> None:
        self.name = f"feishu:{binding_id}"
        self.is_running = False
        self.attachment_cleanup_healthy = True
        self.fail_stop = True

    async def start(self) -> None:
        self.is_running = True

    async def stop(self) -> None:
        if self.fail_stop:
            raise RuntimeError("connection did not close")
        self.is_running = False


@pytest.mark.asyncio
async def test_lifespan_allows_supervisor_to_drain_after_standard_hook_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production wrapper must not cancel Supervisor's longer ownership drain."""
    gateway_app = importlib.import_module("app.gateway.app")
    lifespan = gateway_app.lifespan

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway-shutdown.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(PublishedAgentRow(id="pa_gateway", owner_user_id="owner-a", slug="gateway", display_name="Gateway", status="published"))
        await session.commit()

    repository = AgentChannelRepository(session_factory)
    secrets = LocalEncryptedSecretStore(tmp_path / "secrets", key=Fernet.generate_key())
    secret_ref = await secrets.put(
        encode_feishu_credentials(
            FeishuCredentials(
                app_secret="secret",
                verification_token="token",
                encrypt_key="encrypt",
            )
        )
    )
    binding = await repository.create(
        agent_id="pa_gateway",
        owner_user_id="owner-a",
        app_id="cli_gateway",
        secret_ref=secret_ref,
    )
    fence = _RuntimeLeaderFence()
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=lambda *_args, **_kwargs: _NeverStartedChannel(),
        runtime_leader_fence=fence,
    )

    original_claim = repository.claim_runtime
    claim_entered = asyncio.Event()
    allow_claim = asyncio.Event()

    async def delayed_claim(agent_id: str, binding_id: str, **kwargs):
        claim_entered.set()
        await allow_claim.wait()
        return await original_claim(agent_id, binding_id, **kwargs)

    monkeypatch.setattr(repository, "claim_runtime", delayed_claim)
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_STARTUP_CONVERGENCE_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setattr("app.channels.supervisor.RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(gateway_app, "_SHUTDOWN_HOOK_TIMEOUT_SECONDS", 0.05)

    fake_service = MagicMock()
    fake_service.bus = MessageBus()
    fake_service.get_status.return_value = {}

    async def fake_start(_config=None):
        return fake_service

    async def fake_stop() -> None:
        return None

    async def fake_admin(_app: FastAPI) -> None:
        return None

    async def release_claim_later() -> None:
        await asyncio.sleep(0.2)
        allow_claim.set()

    app = FastAPI()
    app.state.agent_channel_repo = repository
    app.state.channel_event_repo = None
    release_task = None
    try:
        with (
            patch("app.gateway.app.get_app_config"),
            patch("app.gateway.app.get_gateway_config", return_value=MagicMock(host="x", port=0)),
            patch("app.gateway.app.langgraph_runtime", _noop_langgraph_runtime),
            patch("app.gateway.app._ensure_admin_user", side_effect=fake_admin),
            patch("app.gateway.scheduler.start_scheduler_loop", return_value=None),
            patch("app.gateway.scheduler.stop_scheduler_loop", side_effect=fake_admin),
            patch("app.gateway.memory_scheduler.start_memory_rollup_loop", return_value=None),
            patch("app.gateway.memory_scheduler.stop_memory_rollup_loop", side_effect=fake_admin),
            patch("app.channels.service.start_channel_service", side_effect=fake_start),
            patch("app.channels.service.stop_channel_service", side_effect=fake_stop),
            patch("app.channels.supervisor.FeishuSupervisor", return_value=supervisor),
            patch("deerflow.publishing.secret_store.get_secret_store", return_value=secrets),
        ):
            async with lifespan(app):
                active = await repository.activate("pa_gateway", binding["id"], owner_user_id="owner-a")
                assert active is not None
                started = await supervisor.start_binding(binding["id"])
                assert started.health == "unhealthy"
                await asyncio.wait_for(claim_entered.wait(), timeout=1.0)
                release_task = asyncio.create_task(release_claim_later())

        current = await repository.get("pa_gateway", binding["id"], owner_user_id="owner-a")
        assert current is not None
        assert current["runtime_lease_token"] is None
        assert supervisor._shutdown_complete is True
        assert fence.held is False
    finally:
        allow_claim.set()
        if release_task is not None:
            await asyncio.gather(release_task, return_exceptions=True)
        try:
            if not supervisor._shutdown_complete:
                await supervisor.shutdown()
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_lifespan_does_not_mark_supervisor_complete_with_quiescing_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway observes shutdown failure while Supervisor retains ownership."""
    gateway_app = importlib.import_module("app.gateway.app")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway-quiescing.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            PublishedAgentRow(
                id="pa_gateway_stop",
                owner_user_id="owner-a",
                slug="gateway-stop",
                display_name="Gateway Stop",
                status="published",
            )
        )
        await session.commit()

    repository = AgentChannelRepository(session_factory)
    secrets = LocalEncryptedSecretStore(tmp_path / "stop-secrets", key=Fernet.generate_key())
    secret_ref = await secrets.put(
        encode_feishu_credentials(
            FeishuCredentials(
                app_secret="secret",
                verification_token="token",
                encrypt_key="encrypt",
            )
        )
    )
    binding = await repository.create(
        agent_id="pa_gateway_stop",
        owner_user_id="owner-a",
        app_id="cli_gateway_stop",
        secret_ref=secret_ref,
    )
    active = await repository.activate(
        "pa_gateway_stop",
        binding["id"],
        owner_user_id="owner-a",
    )
    assert active is not None

    channel = _FailingStopChannel(binding_id=binding["id"])
    fence = _RuntimeLeaderFence()
    supervisor = FeishuSupervisor(
        repository,
        secrets,
        MessageBus(),
        channel_factory=lambda *_args, **_kwargs: channel,
        runtime_leader_fence=fence,
    )
    monkeypatch.setattr(
        "app.channels.supervisor.RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS",
        0.05,
    )
    monkeypatch.setattr(
        "app.channels.supervisor.RUNTIME_LEASE_HEARTBEAT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(gateway_app, "_SHUTDOWN_HOOK_TIMEOUT_SECONDS", 0.1)

    fake_service = MagicMock()
    fake_service.bus = MessageBus()
    fake_service.get_status.return_value = {}

    async def fake_start(_config=None):
        return fake_service

    async def fake_stop() -> None:
        return None

    async def fake_app_stop(_app: FastAPI) -> None:
        return None

    app = FastAPI()
    app.state.agent_channel_repo = repository
    app.state.channel_event_repo = None
    try:
        with (
            patch("app.gateway.app.get_app_config"),
            patch(
                "app.gateway.app.get_gateway_config",
                return_value=MagicMock(host="x", port=0),
            ),
            patch("app.gateway.app.langgraph_runtime", _noop_langgraph_runtime),
            patch("app.gateway.app._ensure_admin_user", side_effect=fake_app_stop),
            patch("app.gateway.scheduler.start_scheduler_loop", return_value=None),
            patch(
                "app.gateway.scheduler.stop_scheduler_loop",
                side_effect=fake_app_stop,
            ),
            patch(
                "app.gateway.memory_scheduler.start_memory_rollup_loop",
                return_value=None,
            ),
            patch(
                "app.gateway.memory_scheduler.stop_memory_rollup_loop",
                side_effect=fake_app_stop,
            ),
            patch(
                "app.channels.service.start_channel_service",
                side_effect=fake_start,
            ),
            patch(
                "app.channels.service.stop_channel_service",
                side_effect=fake_stop,
            ),
            patch("app.channels.supervisor.FeishuSupervisor", return_value=supervisor),
            patch(
                "deerflow.publishing.secret_store.get_secret_store",
                return_value=secrets,
            ),
        ):
            async with gateway_app.lifespan(app):
                assert supervisor.running_binding_ids == (binding["id"],)

        assert supervisor._shutdown_complete is False
        assert supervisor.owned_binding_ids == (binding["id"],)
        assert fence.held is True

        channel.fail_stop = False
        monkeypatch.setattr(
            "app.channels.supervisor.RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS",
            1.0,
        )
        await supervisor.shutdown()
        assert supervisor._shutdown_complete is True
        assert supervisor.owned_binding_ids == ()
        assert fence.held is False
    finally:
        channel.fail_stop = False
        try:
            if not supervisor._shutdown_complete:
                await supervisor.shutdown()
        finally:
            await engine.dispose()
