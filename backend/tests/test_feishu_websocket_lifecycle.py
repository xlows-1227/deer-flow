from __future__ import annotations

import asyncio
import threading

import lark_oapi as lark
import lark_oapi.ws.client as lark_ws_module
import pytest

from app.channels.feishu import FeishuChannel, _LarkWebSocketSession
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
    async def claim(self, binding_id: str, event_id: str, *, system_scope: object) -> bool:
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
async def test_start_reports_ready_before_attachment_recovery_finishes() -> None:
    session = _BlockingSession()
    channel = _channel(session)
    recovery_started = asyncio.Event()
    release_recovery = asyncio.Event()

    async def blocked_recovery() -> int:
        recovery_started.set()
        await release_recovery.wait()
        return 0

    channel.recover_published_attachment_cleanups = blocked_recovery
    start_task = asyncio.create_task(channel.start())
    try:
        assert await asyncio.to_thread(session.started.wait, 1.0)
        session.allow_ready.set()
        await asyncio.wait_for(start_task, timeout=0.2)
        await asyncio.wait_for(recovery_started.wait(), timeout=0.2)
        assert channel.is_running is True
    finally:
        release_recovery.set()
        session.allow_ready.set()
        if not start_task.done():
            await start_task
        await channel.stop()


@pytest.mark.asyncio
async def test_start_does_not_wait_for_unbounded_binding_index_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.channels import feishu as feishu_module

    session = _BlockingSession()
    channel = _channel(session)
    release_projection = threading.Event()

    def blocked_projection(*_args, **_kwargs) -> tuple[bool, bool]:
        release_projection.wait()
        return False, False

    monkeypatch.setattr(feishu_module, "_binding_cleanup_index_has_backlog", blocked_projection)
    start_task = asyncio.create_task(channel.start())
    try:
        assert await asyncio.to_thread(session.started.wait, 0.2)
        session.allow_ready.set()
        await asyncio.wait_for(start_task, timeout=0.5)
        assert channel.is_running is True
    finally:
        release_projection.set()
        session.allow_ready.set()
        if not start_task.done():
            await start_task
        await channel.stop()


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


def test_two_sdk_sessions_share_one_owned_loop_and_stop_independently(monkeypatch) -> None:
    clients: dict[str, object] = {}
    received: list[tuple[str, str, asyncio.AbstractEventLoop]] = []

    class _Connection:
        async def close(self) -> None:
            return None

    class _SdkClient:
        def __init__(self, *, app_id, event_handler, **_kwargs) -> None:
            self.app_id = app_id
            self.event_handler = event_handler
            self._conn = None
            self._inbox: asyncio.Queue[str] | None = None
            self.loop: asyncio.AbstractEventLoop | None = None
            clients[app_id] = self

        async def _connect(self) -> None:
            self.loop = asyncio.get_running_loop()
            self._inbox = asyncio.Queue()
            self._conn = _Connection()
            lark_ws_module.loop.create_task(self._receive_message_loop())

        async def _receive_message_loop(self) -> None:
            assert self._inbox is not None
            while True:
                payload = await self._inbox.get()
                lark_ws_module.loop.call_soon(self.event_handler, payload)

        async def _ping_loop(self) -> None:
            await asyncio.Event().wait()

        async def _disconnect(self) -> None:
            if self._conn is not None:
                await self._conn.close()
            self._conn = None

        def deliver(self, payload: str) -> None:
            assert self.loop is not None and self._inbox is not None
            self.loop.call_soon_threadsafe(self._inbox.put_nowait, payload)

    monkeypatch.setattr(lark.ws, "Client", _SdkClient)

    async def resolve_endpoint(_client) -> str:
        return "wss://example.test/ws?device_id=device&service_id=1"

    first_ready = threading.Event()
    second_ready = threading.Event()
    first = _LarkWebSocketSession(
        app_id="app-1",
        app_secret="secret-1",
        domain="https://open.feishu.cn",
        event_handler=lambda payload: received.append(("app-1", payload, asyncio.get_running_loop())),
        endpoint_resolver=resolve_endpoint,
    )
    second = _LarkWebSocketSession(
        app_id="app-2",
        app_secret="secret-2",
        domain="https://open.feishu.cn",
        event_handler=lambda payload: received.append(("app-2", payload, asyncio.get_running_loop())),
        endpoint_resolver=resolve_endpoint,
    )
    first_thread = threading.Thread(
        target=first.run,
        kwargs={"on_ready": first_ready.set, "on_error": lambda _detail: None},
    )
    second_thread = threading.Thread(
        target=second.run,
        kwargs={"on_ready": second_ready.set, "on_error": lambda _detail: None},
    )
    first_thread.start()
    second_thread.start()
    try:
        assert first_ready.wait(2.0)
        assert second_ready.wait(2.0)
        first_client = clients["app-1"]
        second_client = clients["app-2"]
        assert first_client.loop is second_client.loop
        assert lark_ws_module.loop is first_client.loop

        first_client.deliver("first-message")
        second_client.deliver("second-message")
        for _ in range(100):
            if len(received) == 2:
                break
            threading.Event().wait(0.01)
        assert {(app_id, payload) for app_id, payload, _loop in received} == {
            ("app-1", "first-message"),
            ("app-2", "second-message"),
        }
        assert all(loop is first_client.loop for _app_id, _payload, loop in received)

        assert first.stop(timeout_seconds=2.0)
        first_thread.join(2.0)
        assert not first_thread.is_alive()
        second_client.deliver("still-running")
        for _ in range(100):
            if len(received) == 3:
                break
            threading.Event().wait(0.01)
        assert received[-1][:2] == ("app-2", "still-running")
        assert second_thread.is_alive()
    finally:
        second.stop(timeout_seconds=2.0)
        first.stop(timeout_seconds=2.0)
        first_thread.join(2.0)
        second_thread.join(2.0)


def test_stalled_endpoint_is_binding_local_and_does_not_block_ready_session(monkeypatch) -> None:
    clients: dict[str, object] = {}
    received = threading.Event()
    first_pinged = threading.Event()
    stalled = asyncio.Event()

    class _Connection:
        async def close(self) -> None:
            return None

    class _SdkClient:
        def __init__(self, *, app_id, event_handler, **_kwargs) -> None:
            self.app_id = app_id
            self.event_handler = event_handler
            self._conn = None
            self._inbox: asyncio.Queue[str] | None = None
            clients[app_id] = self

        async def _connect(self) -> None:
            self._inbox = asyncio.Queue()
            self._conn = _Connection()
            lark_ws_module.loop.create_task(self._receive_message_loop())

        async def _receive_message_loop(self) -> None:
            assert self._inbox is not None
            while True:
                payload = await self._inbox.get()
                self.event_handler(payload)

        async def _ping_loop(self) -> None:
            first_pinged.set()
            await asyncio.Event().wait()

        async def _disconnect(self) -> None:
            self._conn = None

        def deliver(self, payload: str) -> None:
            assert self._inbox is not None
            lark_ws_module.loop.call_soon_threadsafe(self._inbox.put_nowait, payload)

    async def resolve_endpoint(client) -> str:
        if client.app_id == "app-stalled":
            await stalled.wait()
        return "wss://example.test/ws?device_id=device&service_id=1"

    monkeypatch.setattr(lark.ws, "Client", _SdkClient)
    first_ready = threading.Event()
    second_error = threading.Event()
    first = _LarkWebSocketSession(
        app_id="app-ready",
        app_secret="secret-1",
        domain="https://open.feishu.cn",
        event_handler=lambda _payload: received.set(),
        endpoint_resolver=resolve_endpoint,
        connect_timeout_seconds=0.2,
    )
    second = _LarkWebSocketSession(
        app_id="app-stalled",
        app_secret="secret-2",
        domain="https://open.feishu.cn",
        event_handler=lambda _payload: None,
        endpoint_resolver=resolve_endpoint,
        connect_timeout_seconds=0.05,
    )
    first_thread = threading.Thread(
        target=first.run,
        kwargs={"on_ready": first_ready.set, "on_error": lambda _detail: None},
    )
    second_thread = threading.Thread(
        target=second.run,
        kwargs={"on_ready": lambda: None, "on_error": lambda _detail: second_error.set()},
    )
    first_thread.start()
    second_thread.start()
    try:
        assert first_ready.wait(1.0)
        assert first_pinged.wait(1.0)
        clients["app-ready"].deliver("still-responsive")
        assert received.wait(1.0)
        assert second_error.wait(1.0)
        second_thread.join(1.0)
        assert not second_thread.is_alive()
        assert first_thread.is_alive()
    finally:
        second.stop(timeout_seconds=1.0)
        first.stop(timeout_seconds=1.0)
        first_thread.join(1.0)
        second_thread.join(1.0)
