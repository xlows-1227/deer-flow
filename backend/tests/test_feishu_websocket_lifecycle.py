from __future__ import annotations

import asyncio
import threading

import pytest

from app.channels.feishu import FeishuChannel
from app.channels.message_bus import MessageBus


class _BlockingSession:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.allow_ready = threading.Event()
        self.release = threading.Event()
        self.exited = threading.Event()

    def run(self, *, on_ready, on_error) -> None:
        self.started.set()
        self.allow_ready.wait()
        on_ready()
        self.release.wait()
        self.exited.set()

    def stop(self, *, timeout_seconds: float) -> bool:
        self.release.set()
        return self.exited.wait(timeout_seconds)


class _FailingSession:
    def run(self, *, on_ready, on_error) -> None:
        on_error("authentication failed")

    def stop(self, *, timeout_seconds: float) -> bool:
        return True


class _Deduplicator:
    async def claim(self, binding_id: str, event_id: str) -> bool:
        return True


def _channel(session, *, runtime_error_callback=None) -> FeishuChannel:
    return FeishuChannel(
        MessageBus(),
        app_id="app-id",
        app_secret="app-secret",
        verification_token="verification-token",
        binding_id="binding-1",
        agent_id="agent-1",
        event_deduplicator=_Deduplicator(),
        websocket_session_factory=lambda **_kwargs: session,
        startup_timeout_seconds=20.0,
        runtime_error_callback=runtime_error_callback,
    )


@pytest.mark.asyncio
async def test_start_waits_for_connection_ready_and_stop_joins_worker() -> None:
    session = _BlockingSession()
    channel = _channel(session)

    start_task = asyncio.create_task(channel.start())
    for _ in range(500):
        if session.started.is_set():
            break
        await asyncio.sleep(0.01)
    started = session.started.is_set()
    assert started is True, (
        channel.websocket_thread_alive,
        start_task.done(),
        start_task.exception() if start_task.done() else None,
    )
    await asyncio.sleep(0)
    assert start_task.done() is False
    assert channel.is_running is False

    session.allow_ready.set()
    await start_task
    assert channel.is_running is True

    await channel.stop()
    assert session.exited.is_set()
    assert channel.is_running is False
    assert channel.websocket_thread_alive is False


@pytest.mark.asyncio
async def test_start_fails_when_websocket_reports_error_before_ready() -> None:
    channel = _channel(_FailingSession())

    with pytest.raises(RuntimeError, match="failed to connect"):
        await channel.start()

    assert channel.is_running is False
    assert channel.websocket_thread_alive is False


@pytest.mark.asyncio
async def test_error_after_ready_notifies_runtime_health_callback() -> None:
    reported = asyncio.Event()
    details: list[str] = []

    async def on_runtime_error(detail: str) -> None:
        details.append(detail)
        reported.set()

    failure = threading.Event()

    class _LateFailureSession:
        def run(self, *, on_ready, on_error) -> None:
            on_ready()
            failure.wait()
            on_error("connection lost")

        def stop(self, *, timeout_seconds: float) -> bool:
            return True

    channel = _channel(_LateFailureSession(), runtime_error_callback=on_runtime_error)
    await channel.start()
    failure.set()
    assert await asyncio.wait_for(reported.wait(), timeout=1.0) is True
    assert details == ["Feishu WebSocket connection lost"]
    assert channel.is_running is False
