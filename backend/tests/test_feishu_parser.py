import asyncio
import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.channels.commands import KNOWN_CHANNEL_COMMANDS
from app.channels.feishu import FEISHU_INBOUND_FILE_MAX_BYTES, FeishuChannel, _read_inbound_resource
from app.channels.message_bus import InboundMessage, MessageBus
from deerflow.config.paths import Paths, get_paths
from deerflow.sandbox.sandbox_provider import SandboxAcquisition


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_feishu_on_message_plain_text():
    bus = MessageBus()
    config = {"app_id": "test", "app_secret": "test"}
    channel = FeishuChannel(bus, config)

    # Create mock event
    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"

    # Plain text content
    content_dict = {"text": "Hello world"}
    event.event.message.content = json.dumps(content_dict)

    # Call _on_message
    channel._on_message(event)

    # Since main_loop isn't running in this synchronous test, we can't easily assert on bus,
    # but we can intercept _make_inbound to check the parsed text.
    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        assert mock_make_inbound.call_args[1]["text"] == "Hello world"


def test_feishu_on_message_rich_text():
    bus = MessageBus()
    config = {"app_id": "test", "app_secret": "test"}
    channel = FeishuChannel(bus, config)

    # Create mock event
    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"

    # Rich text content (topic group / post)
    content_dict = {"content": [[{"tag": "text", "text": "Paragraph 1, part 1."}, {"tag": "text", "text": "Paragraph 1, part 2."}], [{"tag": "at", "text": "@bot"}, {"tag": "text", "text": " Paragraph 2."}]]}
    event.event.message.content = json.dumps(content_dict)

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        parsed_text = mock_make_inbound.call_args[1]["text"]

        # Expected text:
        # Paragraph 1, part 1. Paragraph 1, part 2.
        #
        # @bot  Paragraph 2.
        assert "Paragraph 1, part 1. Paragraph 1, part 2." in parsed_text
        assert "@bot  Paragraph 2." in parsed_text
        assert "\n\n" in parsed_text


def test_feishu_receive_file_replaces_placeholders_in_order():
    async def go():
        bus = MessageBus()
        channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})

        msg = InboundMessage(
            channel_name="feishu",
            chat_id="chat_1",
            user_id="user_1",
            text="before [image] middle [file] after",
            thread_ts="msg_1",
            files=[{"image_key": "img_key"}, {"file_key": "file_key"}],
        )

        channel._receive_single_file = AsyncMock(side_effect=["/mnt/user-data/uploads/a.png", "/mnt/user-data/uploads/b.pdf"])

        result = await channel.receive_file(msg, "thread_1")

        assert result.text == "before /mnt/user-data/uploads/a.png middle /mnt/user-data/uploads/b.pdf after"

    _run(go())


def test_feishu_inbound_resource_reader_enforces_size_limit():
    allowed = b"a" * FEISHU_INBOUND_FILE_MAX_BYTES
    assert _read_inbound_resource(io.BytesIO(allowed)) == allowed

    with pytest.raises(ValueError, match="size limit"):
        _read_inbound_resource(io.BytesIO(allowed + b"b"))


class _ChunkOnlyAsyncStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.yielded = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _published_resource_client_factory(
    stream: httpx.AsyncByteStream,
    *,
    content_length: int | None,
):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant-token"},
                request=request,
            )
        assert request.headers["authorization"] == "Bearer tenant-token"
        headers = {"content-disposition": 'attachment; filename="input.bin"'}
        if content_length is not None:
            headers["content-length"] = str(content_length)
        return httpx.Response(200, headers=headers, stream=stream, request=request)

    transport = httpx.MockTransport(handler)
    return lambda: httpx.AsyncClient(transport=transport)


@pytest.mark.asyncio
async def test_published_feishu_http_transport_streams_exact_boundary_without_full_body_read(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    monkeypatch.setattr(
        "app.channels.feishu.get_sandbox_provider",
        lambda: SimpleNamespace(acquire=lambda _thread_id, *, user_id: "local"),
    )
    stream = _ChunkOnlyAsyncStream([b"abc", b"def"])
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        published_http_client_factory=_published_resource_client_factory(
            stream,
            content_length=6,
        ),
    )

    materialized = await channel._materialize_published_file(
        "message-1",
        "file-1",
        "file",
        "thread-1",
        owner_user_id="owner-a",
        max_bytes=6,
    )

    assert materialized.actual_path.read_bytes() == b"abcdef"
    assert materialized.size == 6
    assert stream.yielded == 2
    assert stream.closed is True


@pytest.mark.asyncio
async def test_published_feishu_stream_stops_at_chunk_limit_and_deletes_partial_file(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    stream = _ChunkOnlyAsyncStream([b"abc", b"def", b"payload-must-not-be-read"])
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        published_http_client_factory=_published_resource_client_factory(
            stream,
            content_length=None,
        ),
    )

    with pytest.raises(ValueError, match="size limit"):
        await channel._materialize_published_file(
            "message-1",
            "file-1",
            "file",
            "thread-1",
            owner_user_id="owner-a",
            max_bytes=5,
        )

    uploads_dir = get_paths().sandbox_uploads_dir("thread-1", user_id="owner-a")
    assert list(uploads_dir.iterdir()) == []
    assert stream.yielded == 2
    assert stream.closed is True


@pytest.mark.asyncio
async def test_published_feishu_content_length_rejects_before_reading_network_stream(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    stream = _ChunkOnlyAsyncStream([b"must-not-be-read"])
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        published_http_client_factory=_published_resource_client_factory(
            stream,
            content_length=6,
        ),
    )

    with pytest.raises(ValueError, match="size limit"):
        await channel._materialize_published_file(
            "message-1",
            "file-1",
            "file",
            "thread-1",
            owner_user_id="owner-a",
            max_bytes=5,
        )

    assert stream.yielded == 0
    assert stream.closed is True


@pytest.mark.asyncio
async def test_cancelling_published_feishu_stream_removes_registered_partial_file(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    blocked = asyncio.Event()
    release = asyncio.Event()

    class _BlockingStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = False

        async def __aiter__(self):
            yield b"partial"
            blocked.set()
            await release.wait()
            yield b"late-data"

        async def aclose(self) -> None:
            self.closed = True

    stream = _BlockingStream()
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        published_http_client_factory=_published_resource_client_factory(
            stream,
            content_length=None,
        ),
    )
    download = asyncio.create_task(
        channel._materialize_published_file(
            "message-1",
            "file-1",
            "file",
            "thread-1",
            owner_user_id="owner-a",
            max_bytes=1_024,
        )
    )
    await asyncio.wait_for(blocked.wait(), timeout=1.0)
    download.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await download
    finally:
        release.set()

    uploads_dir = get_paths().sandbox_uploads_dir("thread-1", user_id="owner-a")
    assert list(uploads_dir.iterdir()) == []
    assert stream.closed is True


@pytest.mark.asyncio
async def test_cancelling_blocked_sandbox_sync_schedules_complete_host_and_remote_cleanup(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    monkeypatch.setattr(
        "app.channels.feishu.FEISHU_SANDBOX_SYNC_CLEANUP_TIMEOUT_SECONDS",
        0.05,
    )
    monkeypatch.setattr(
        "app.channels.feishu.FEISHU_ATTACHMENT_CLEANUP_DRAIN_TIMEOUT_SECONDS",
        0.02,
    )
    sync_started = threading.Event()
    release_sync = threading.Event()
    cleanup_finished = threading.Event()
    remote_files: set[str] = set()
    delete_attempts = 0

    class _RemoteSandbox:
        def update_file_from_path(self, virtual_path: str, _source_path: str) -> None:
            sync_started.set()
            release_sync.wait()
            remote_files.add(virtual_path)

        def delete_file(self, virtual_path: str) -> None:
            nonlocal delete_attempts
            delete_attempts += 1
            if delete_attempts == 1:
                raise RuntimeError("transient sandbox delete failure")
            remote_files.discard(virtual_path)
            cleanup_finished.set()

    sandbox = _RemoteSandbox()

    class _RemoteProvider:
        uses_thread_data_mounts = False

        def acquire(self, _thread_id: str, *, user_id: str) -> str:
            raise AssertionError(f"event loop called blocking acquire for {user_id}")

        async def acquire_async(self, thread_id: str, *, user_id: str) -> str:
            assert thread_id == "thread-1"
            assert user_id == "owner-a"
            return "remote-1"

        def get(self, sandbox_id: str):
            assert sandbox_id == "remote-1"
            return sandbox

    monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", _RemoteProvider)
    stream = _ChunkOnlyAsyncStream([b"complete"])
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        published_http_client_factory=_published_resource_client_factory(
            stream,
            content_length=8,
        ),
    )
    message = InboundMessage(
        channel_name="feishu:binding-1",
        chat_id="chat-1",
        user_id="user-1",
        text="review [file]",
        thread_ts="message-1",
        files=[{"file_key": "file-1"}],
    )
    materialize = asyncio.create_task(
        channel.materialize_published_files(
            message,
            "thread-1",
            owner_user_id="owner-a",
            max_input_bytes=1_024,
        )
    )
    assert await asyncio.to_thread(sync_started.wait, 1.0)
    materialize.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(materialize, timeout=1.0)
    await channel.stop()
    release_sync.set()
    assert await asyncio.to_thread(cleanup_finished.wait, 1.0)

    uploads_dir = get_paths().sandbox_uploads_dir("thread-1", user_id="owner-a")
    assert list(uploads_dir.iterdir()) == []
    assert remote_files == set()
    assert delete_attempts == 2


@pytest.mark.asyncio
async def test_published_attachment_cleanup_outbox_recovers_after_binding_restart(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    monkeypatch.setattr(
        "app.channels.feishu.FEISHU_SANDBOX_SYNC_CLEANUP_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        "app.channels.feishu.FEISHU_ATTACHMENT_DELETE_MAX_ATTEMPTS",
        1,
    )
    sync_started = threading.Event()
    release_sync = threading.Event()
    remote_files: set[str] = set()
    delete_fails = True
    delete_attempted = threading.Event()

    class _RemoteSandbox:
        def update_file_from_path(self, virtual_path: str, _source_path: str) -> None:
            sync_started.set()
            release_sync.wait()
            remote_files.add(virtual_path)

        def delete_file(self, virtual_path: str) -> None:
            if delete_fails:
                delete_attempted.set()
                raise RuntimeError("sandbox temporarily unavailable")
            remote_files.discard(virtual_path)

    sandbox = _RemoteSandbox()

    class _RemoteProvider:
        uses_thread_data_mounts = False

        async def acquire_async(self, thread_id: str, *, user_id: str) -> str:
            assert (thread_id, user_id) == ("thread-recovery", "owner-a")
            return "remote-recovery"

        def get(self, sandbox_id: str):
            assert sandbox_id == "remote-recovery"
            return sandbox

    monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", _RemoteProvider)
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        binding_id="binding-recovery",
        published_http_client_factory=_published_resource_client_factory(
            _ChunkOnlyAsyncStream([b"complete"]),
            content_length=8,
        ),
    )
    message = InboundMessage(
        channel_name="feishu:binding-recovery",
        chat_id="chat-1",
        user_id="user-1",
        text="review [file]",
        thread_ts="message-recovery",
        files=[{"file_key": "file-1"}],
    )
    materialize = asyncio.create_task(
        channel.materialize_published_files(
            message,
            "thread-recovery",
            owner_user_id="owner-a",
            max_input_bytes=1_024,
        )
    )
    assert await asyncio.to_thread(sync_started.wait, 1.0)
    materialize.cancel()
    with pytest.raises(asyncio.CancelledError):
        await materialize
    release_sync.set()
    assert await asyncio.to_thread(delete_attempted.wait, 1.0)
    await asyncio.sleep(0.02)

    outbox_dir = tmp_path / "published-attachment-cleanup"
    assert len(list(outbox_dir.glob("*.json"))) == 1
    assert remote_files == {"/mnt/user-data/uploads/input.bin"}
    assert channel.attachment_cleanup_healthy is False

    delete_fails = False
    restarted_channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        binding_id="binding-recovery",
    )
    assert await restarted_channel.recover_published_attachment_cleanups() == 1
    assert list(outbox_dir.glob("*.json")) == []
    assert remote_files == set()
    assert restarted_channel.attachment_cleanup_healthy is True


@pytest.mark.asyncio
async def test_attachment_recovery_waits_for_live_sync_producer(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    monkeypatch.setattr(
        "app.channels.feishu.FEISHU_SANDBOX_SYNC_CLEANUP_TIMEOUT_SECONDS",
        0.01,
    )
    sync_started = threading.Event()
    release_sync = threading.Event()
    cleanup_finished = threading.Event()
    remote_files: set[str] = set()

    class _RemoteSandbox:
        def update_file_from_path(self, virtual_path: str, _source_path: str) -> None:
            remote_files.add(virtual_path)
            sync_started.set()
            release_sync.wait()
            # A blocked producer may write again after a concurrent delete.
            remote_files.add(virtual_path)

        def delete_file(self, virtual_path: str) -> None:
            remote_files.discard(virtual_path)
            if release_sync.is_set():
                cleanup_finished.set()

    sandbox = _RemoteSandbox()

    class _RemoteProvider:
        uses_thread_data_mounts = False

        async def acquire_async(self, thread_id: str, *, user_id: str) -> str:
            assert (thread_id, user_id) == ("thread-fenced", "owner-a")
            return "remote-fenced"

        def get(self, sandbox_id: str):
            assert sandbox_id == "remote-fenced"
            return sandbox

    monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", _RemoteProvider)
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        binding_id="binding-fenced",
        published_http_client_factory=_published_resource_client_factory(
            _ChunkOnlyAsyncStream([b"complete"]),
            content_length=8,
        ),
    )
    message = InboundMessage(
        channel_name="feishu:binding-fenced",
        chat_id="chat-1",
        user_id="user-1",
        text="review [file]",
        thread_ts="message-fenced",
        files=[{"file_key": "file-1"}],
    )

    materialize = asyncio.create_task(
        channel.materialize_published_files(
            message,
            "thread-fenced",
            owner_user_id="owner-a",
            max_input_bytes=1_024,
        )
    )
    assert await asyncio.to_thread(sync_started.wait, 1.0)
    materialize.cancel()
    with pytest.raises(asyncio.CancelledError):
        await materialize

    outbox_path = next((tmp_path / "published-attachment-cleanup").glob("*.json"))
    try:
        payload = json.loads(outbox_path.read_text(encoding="utf-8"))
        assert payload["phase"] == "producer_pending"

        restarted_channel = FeishuChannel(
            MessageBus(),
            {"app_id": "test", "app_secret": "test"},
            binding_id="binding-fenced",
        )
        assert await restarted_channel.recover_published_attachment_cleanups() == 0
        assert outbox_path.exists()
        assert remote_files == {"/mnt/user-data/uploads/input.bin"}
    finally:
        release_sync.set()

    assert await asyncio.to_thread(cleanup_finished.wait, 1.0)
    for _ in range(100):
        if not outbox_path.exists():
            break
        await asyncio.sleep(0.01)
    assert not outbox_path.exists()
    assert remote_files == set()


@pytest.mark.asyncio
async def test_mounted_cleanup_failure_keeps_binding_unhealthy_until_retry(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))

    class _MountedProvider:
        uses_thread_data_mounts = True

        async def acquire_async(self, thread_id: str, *, user_id: str) -> str:
            assert (thread_id, user_id) == ("thread-mounted", "owner-a")
            return "local:thread-mounted"

    monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", _MountedProvider)
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        binding_id="binding-mounted",
    )
    host_path = get_paths().sandbox_uploads_dir("thread-mounted", user_id="owner-a") / "input.bin"
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_bytes(b"pending")
    await channel._persist_attachment_cleanup_job(
        thread_id="thread-mounted",
        owner_user_id="owner-a",
        files=[
            SimpleNamespace(
                virtual_path="/mnt/user-data/uploads/input.bin",
                actual_path=host_path,
            )
        ],
    )

    async def fail_delete(_files) -> bool:
        return False

    monkeypatch.setattr(channel, "_delete_published_host_files", fail_delete)
    assert await channel.recover_published_attachment_cleanups() == 0
    assert channel.attachment_cleanup_healthy is False
    assert len(list((tmp_path / "published-attachment-cleanup").glob("*.json"))) == 1

    async def delete_files(files) -> bool:
        for materialized in files:
            materialized.actual_path.unlink(missing_ok=True)
        return True

    monkeypatch.setattr(channel, "_delete_published_host_files", delete_files)
    assert await channel.recover_published_attachment_cleanups() == 1
    assert channel.attachment_cleanup_healthy is True


@pytest.mark.asyncio
async def test_cleanup_recovery_continues_after_health_projection_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.channels.feishu.FEISHU_ATTACHMENT_CLEANUP_RETRY_INTERVAL_SECONDS",
        0.01,
    )
    projected = asyncio.Event()
    recoveries = 0
    projections = 0

    async def project_health(_healthy: bool, _detail: str | None) -> None:
        nonlocal projections
        projections += 1
        if projections == 1:
            raise RuntimeError("database temporarily unavailable")
        projected.set()

    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        binding_id="binding-health-retry",
        runtime_health_callback=project_health,
    )

    async def recover() -> int:
        nonlocal recoveries
        recoveries += 1
        return 0

    monkeypatch.setattr(channel, "recover_published_attachment_cleanups", recover)
    recovery_loop = asyncio.create_task(channel._retry_published_attachment_cleanups())
    try:
        await asyncio.wait_for(projected.wait(), timeout=0.2)
    finally:
        channel._stop_requested = True
        recovery_loop.cancel()
        await asyncio.gather(recovery_loop, return_exceptions=True)

    assert recoveries >= 2
    assert projections >= 2


@pytest.mark.asyncio
async def test_cleanup_recovery_enforces_total_budget_on_stalled_remote_delete(
    tmp_path,
    monkeypatch,
) -> None:
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    monkeypatch.setattr("app.channels.feishu.FEISHU_ATTACHMENT_RECOVERY_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr("app.channels.feishu.FEISHU_ATTACHMENT_DELETE_TIMEOUT_SECONDS", 1.0)
    delete_started = threading.Event()
    release_delete = threading.Event()

    class _RemoteSandbox:
        def delete_file(self, _virtual_path: str) -> None:
            delete_started.set()
            release_delete.wait()

    class _RemoteProvider:
        uses_thread_data_mounts = False

        async def acquire_async(self, _thread_id: str, *, user_id: str) -> str:
            assert user_id == "owner-a"
            return "remote-stalled-delete"

        def get(self, _sandbox_id: str):
            return _RemoteSandbox()

    monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", _RemoteProvider)
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        binding_id="binding-stalled-delete",
    )
    host_path = get_paths().sandbox_uploads_dir("thread-stalled-delete", user_id="owner-a") / "input.bin"
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_bytes(b"pending")
    await channel._persist_attachment_cleanup_job(
        thread_id="thread-stalled-delete",
        owner_user_id="owner-a",
        files=[
            SimpleNamespace(
                virtual_path="/mnt/user-data/uploads/input.bin",
                actual_path=host_path,
            )
        ],
    )

    try:
        assert (
            await asyncio.wait_for(
                channel.recover_published_attachment_cleanups(),
                timeout=0.4,
            )
            == 0
        )
        assert await asyncio.to_thread(delete_started.wait, 0.2)
        assert channel.attachment_cleanup_healthy is False
        assert len(list((tmp_path / "published-attachment-cleanup").glob("*.json"))) == 1
    finally:
        release_delete.set()


@pytest.mark.asyncio
async def test_cleanup_recovery_prioritizes_ready_job_after_active_producers(
    tmp_path,
    monkeypatch,
) -> None:
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))

    class _MountedProvider:
        uses_thread_data_mounts = True

        async def acquire_async(self, thread_id: str, *, user_id: str) -> str:
            return f"local:{thread_id}:{user_id}"

    monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", _MountedProvider)
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        binding_id="binding-fair-recovery",
    )
    outbox_dir = tmp_path / "published-attachment-cleanup"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    for index in range(25):
        (outbox_dir / f"00-producer-{index:02d}.json").write_text(
            json.dumps(
                {
                    "job_id": f"00-producer-{index:02d}",
                    "binding_id": "binding-fair-recovery",
                    "thread_id": f"thread-producer-{index:02d}",
                    "owner_user_id": "owner-a",
                    "virtual_paths": ["/mnt/user-data/uploads/input.bin"],
                    "phase": "producer_pending",
                    "producer_token": f"producer-{index:02d}",
                    "producer_lease_expires_at": 9_999_999_999.0,
                    "claim_token": None,
                    "claim_lease_expires_at": None,
                    "version": 1,
                }
            ),
            encoding="utf-8",
        )
    ready_path = get_paths().sandbox_uploads_dir("thread-ready-26", user_id="owner-a") / "input.bin"
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    ready_path.write_bytes(b"pending")
    (outbox_dir / "zz-ready-26.json").write_text(
        json.dumps(
            {
                "job_id": "zz-ready-26",
                "binding_id": "binding-fair-recovery",
                "thread_id": "thread-ready-26",
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

    assert await channel.recover_published_attachment_cleanups() == 1
    assert not (outbox_dir / "zz-ready-26.json").exists()
    assert not ready_path.exists()


@pytest.mark.asyncio
async def test_global_cleanup_pass_caps_total_jobs_across_bindings(
    tmp_path,
    monkeypatch,
) -> None:
    import deerflow.config.paths as paths_module
    from app.channels.feishu import recover_all_published_attachment_cleanups

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))

    class _MountedProvider:
        uses_thread_data_mounts = True

        async def acquire_async(self, thread_id: str, *, user_id: str) -> str:
            return f"local:{thread_id}:{user_id}"

    monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", _MountedProvider)
    outbox_dir = tmp_path / "published-attachment-cleanup"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    for index in range(30):
        thread_id = f"thread-global-{index:02d}"
        host_path = get_paths().sandbox_uploads_dir(thread_id, user_id="owner-a") / "input.bin"
        host_path.parent.mkdir(parents=True, exist_ok=True)
        host_path.write_bytes(b"pending")
        job_id = f"global-ready-{index:02d}"
        (outbox_dir / f"{job_id}.json").write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "binding_id": f"binding-{index % 3}",
                    "thread_id": thread_id,
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

    assert await recover_all_published_attachment_cleanups() == 25
    assert len(list(outbox_dir.glob("*.json"))) == 5
    assert await recover_all_published_attachment_cleanups() == 5
    assert list(outbox_dir.glob("*.json")) == []


@pytest.mark.asyncio
async def test_cleanup_recovery_budget_includes_outbox_discovery(monkeypatch) -> None:
    monkeypatch.setattr("app.channels.feishu.FEISHU_ATTACHMENT_RECOVERY_TIMEOUT_SECONDS", 0.05)
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        binding_id="binding-slow-discovery",
    )

    def slow_scan():
        threading.Event().wait(0.2)
        return []

    monkeypatch.setattr(channel, "_read_attachment_cleanup_jobs", slow_scan)

    assert (
        await asyncio.wait_for(
            channel.recover_published_attachment_cleanups(),
            timeout=0.15,
        )
        == 0
    )
    assert channel.attachment_cleanup_healthy is False


@pytest.mark.asyncio
async def test_old_cleanup_snapshot_cannot_clear_health_after_new_job(
    tmp_path,
    monkeypatch,
) -> None:
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))

    class _MountedProvider:
        uses_thread_data_mounts = True

    monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", _MountedProvider)
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        binding_id="binding-health-generation",
    )
    old_host_path = get_paths().sandbox_uploads_dir("thread-old-health", user_id="owner-a") / "old.bin"
    old_host_path.parent.mkdir(parents=True, exist_ok=True)
    old_host_path.write_bytes(b"old")
    old_job = await channel._persist_attachment_cleanup_job(
        thread_id="thread-old-health",
        owner_user_id="owner-a",
        files=[SimpleNamespace(virtual_path="/mnt/user-data/uploads/old.bin", actual_path=old_host_path)],
    )
    old_execution_paused = asyncio.Event()
    release_old_execution = asyncio.Event()

    async def finish_old_job(job, _provider, *, acquire_timeout_seconds, refresh_health):
        assert job.job_id == old_job.job_id
        assert acquire_timeout_seconds > 0
        assert refresh_health is False
        channel._attachment_cleanup_job_path(job.job_id).unlink()
        old_execution_paused.set()
        await release_old_execution.wait()
        return True

    monkeypatch.setattr(channel, "_recover_attachment_cleanup_job", finish_old_job)
    old_recovery = asyncio.create_task(channel.recover_published_attachment_cleanups())
    await asyncio.wait_for(old_execution_paused.wait(), timeout=1.0)

    new_host_path = get_paths().sandbox_uploads_dir("thread-new-health", user_id="owner-a") / "new.bin"
    new_host_path.parent.mkdir(parents=True, exist_ok=True)
    new_host_path.write_bytes(b"new")
    await channel._persist_attachment_cleanup_job(
        thread_id="thread-new-health",
        owner_user_id="owner-a",
        files=[SimpleNamespace(virtual_path="/mnt/user-data/uploads/new.bin", actual_path=new_host_path)],
    )
    release_old_execution.set()

    assert await old_recovery == 1
    assert channel.attachment_cleanup_healthy is False


@pytest.mark.asyncio
async def test_stalled_sandbox_acquire_does_not_block_another_binding(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    acquire_started = asyncio.Event()
    release_acquire = asyncio.Event()
    released_sandboxes: list[str] = []

    class _AsyncProvider:
        uses_thread_data_mounts = True

        def acquire(self, _thread_id: str, *, user_id: str) -> str:
            raise AssertionError(f"blocking acquire used for {user_id}")

        async def acquire_async(self, thread_id: str, *, user_id: str) -> str:
            assert user_id == "owner-a"
            if thread_id == "thread-stalled":
                acquire_started.set()
                await release_acquire.wait()
            return f"local:{thread_id}"

        def release(self, sandbox_id: str) -> None:
            released_sandboxes.append(sandbox_id)

    monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", _AsyncProvider)

    def new_channel(payload: bytes) -> FeishuChannel:
        return FeishuChannel(
            MessageBus(),
            {"app_id": "test", "app_secret": "test"},
            published_http_client_factory=_published_resource_client_factory(
                _ChunkOnlyAsyncStream([payload]),
                content_length=len(payload),
            ),
        )

    stalled_channel = new_channel(b"stalled")
    healthy_channel = new_channel(b"healthy")
    stalled_message = InboundMessage(
        channel_name="feishu:binding-stalled",
        chat_id="chat-1",
        user_id="user-1",
        text="review [file]",
        thread_ts="message-stalled",
        files=[{"file_key": "file-stalled"}],
    )
    healthy_message = InboundMessage(
        channel_name="feishu:binding-healthy",
        chat_id="chat-2",
        user_id="user-2",
        text="review [file]",
        thread_ts="message-healthy",
        files=[{"file_key": "file-healthy"}],
    )
    stalled = asyncio.create_task(
        stalled_channel.materialize_published_files(
            stalled_message,
            "thread-stalled",
            owner_user_id="owner-a",
            max_input_bytes=1_024,
        )
    )
    await asyncio.wait_for(acquire_started.wait(), timeout=1.0)

    prepared, byte_count = await asyncio.wait_for(
        healthy_channel.materialize_published_files(
            healthy_message,
            "thread-healthy",
            owner_user_id="owner-a",
            max_input_bytes=1_024,
        ),
        timeout=0.2,
    )
    await asyncio.wait_for(healthy_channel.stop(), timeout=0.2)
    assert byte_count == 7
    assert prepared.text == "review /mnt/user-data/uploads/input.bin"

    stalled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stalled
    release_acquire.set()
    await asyncio.wait_for(stalled_channel.stop(), timeout=0.2)
    assert released_sandboxes == []


@pytest.mark.asyncio
async def test_published_sandbox_acquire_deadline_releases_late_capacity(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    monkeypatch.setattr(
        "app.channels.feishu.FEISHU_SANDBOX_ACQUIRE_TIMEOUT_SECONDS",
        0.01,
    )
    acquire_started = asyncio.Event()
    release_acquire = asyncio.Event()
    abandoned_sandboxes: list[str] = []

    class _SlowProvider:
        uses_thread_data_mounts = True

        async def acquire_with_lease_async(
            self,
            thread_id: str,
            *,
            user_id: str,
        ) -> SandboxAcquisition:
            assert (thread_id, user_id) == ("thread-acquire-timeout", "owner-a")
            acquire_started.set()
            await release_acquire.wait()
            return SandboxAcquisition(
                sandbox_id="local:thread-acquire-timeout",
                acquisition_token="late-operation",
                thread_id=thread_id,
                release_on_abandon=True,
            )

        def accept_acquisition(self, _acquisition: SandboxAcquisition) -> None:
            raise AssertionError("timed-out acquisition must not be accepted")

        def abandon_acquisition(self, acquisition: SandboxAcquisition) -> None:
            abandoned_sandboxes.append(acquisition.sandbox_id)

        def release(self, _sandbox_id: str) -> None:
            raise AssertionError("late compensation must not release a naked sandbox id")

    monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", _SlowProvider)
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        published_http_client_factory=_published_resource_client_factory(
            _ChunkOnlyAsyncStream([b"complete"]),
            content_length=8,
        ),
    )
    message = InboundMessage(
        channel_name="feishu:binding-1",
        chat_id="chat-1",
        user_id="user-1",
        text="review [file]",
        thread_ts="message-acquire-timeout",
        files=[{"file_key": "file-1"}],
    )

    with pytest.raises(TimeoutError, match="sandbox acquisition"):
        await asyncio.wait_for(
            channel.materialize_published_files(
                message,
                "thread-acquire-timeout",
                owner_user_id="owner-a",
                max_input_bytes=1_024,
            ),
            timeout=0.2,
        )
    await asyncio.wait_for(acquire_started.wait(), timeout=0.2)
    uploads_dir = get_paths().sandbox_uploads_dir("thread-acquire-timeout", user_id="owner-a")
    assert list(uploads_dir.iterdir()) == []

    release_acquire.set()
    await asyncio.wait_for(channel.stop(), timeout=0.2)
    assert abandoned_sandboxes == ["local:thread-acquire-timeout"]


@pytest.mark.asyncio
async def test_never_returning_sandbox_acquisition_is_cancelled_after_final_deadline(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    monkeypatch.setattr("app.channels.feishu.FEISHU_SANDBOX_ACQUIRE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.channels.feishu.FEISHU_SANDBOX_LATE_ACQUIRE_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr("app.channels.feishu.FEISHU_SANDBOX_LATE_ACQUIRE_CANCEL_DRAIN_SECONDS", 0.02)
    cancelled = asyncio.Event()

    class _NeverProvider:
        uses_thread_data_mounts = True

        async def acquire_with_lease_async(
            self,
            _thread_id: str,
            *,
            user_id: str,
        ) -> SandboxAcquisition:
            assert user_id == "owner-a"
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        def accept_acquisition(self, _acquisition: SandboxAcquisition) -> None:
            raise AssertionError("never-returning acquisition cannot be accepted")

        def abandon_acquisition(self, _acquisition: SandboxAcquisition) -> None:
            raise AssertionError("no completed acquisition exists to abandon")

    monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", _NeverProvider)
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        binding_id="binding-never-acquire",
        published_http_client_factory=_published_resource_client_factory(
            _ChunkOnlyAsyncStream([b"complete"]),
            content_length=8,
        ),
    )
    message = InboundMessage(
        channel_name="feishu:binding-never-acquire",
        chat_id="chat-1",
        user_id="user-1",
        text="review [file]",
        thread_ts="message-never-acquire",
        files=[{"file_key": "file-1"}],
    )

    with pytest.raises(TimeoutError, match="sandbox acquisition"):
        await channel.materialize_published_files(
            message,
            "thread-never-acquire",
            owner_user_id="owner-a",
            max_input_bytes=1_024,
        )
    await asyncio.wait_for(cancelled.wait(), timeout=0.2)
    for _ in range(20):
        if not channel._cleanup_tasks:
            break
        await asyncio.sleep(0.01)
    assert channel._cleanup_tasks == set()
    assert channel.attachment_cleanup_healthy is False


@pytest.mark.asyncio
async def test_published_feishu_sandbox_sync_deadline_defers_complete_cleanup(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    monkeypatch.setattr(
        "app.channels.feishu.FEISHU_SANDBOX_SYNC_FILE_TIMEOUT_SECONDS",
        0.05,
        raising=False,
    )
    monkeypatch.setattr(
        "app.channels.feishu.FEISHU_SANDBOX_SYNC_BATCH_TIMEOUT_SECONDS",
        0.05,
        raising=False,
    )
    monkeypatch.setattr(
        "app.channels.feishu.FEISHU_SANDBOX_SYNC_CLEANUP_TIMEOUT_SECONDS",
        0.01,
    )
    sync_started = threading.Event()
    release_sync = threading.Event()
    cleanup_finished = threading.Event()
    remote_files: set[str] = set()

    class _RemoteSandbox:
        def update_file_from_path(self, virtual_path: str, _source_path: str) -> None:
            sync_started.set()
            release_sync.wait()
            remote_files.add(virtual_path)

        def delete_file(self, virtual_path: str) -> None:
            remote_files.discard(virtual_path)
            cleanup_finished.set()

    sandbox = _RemoteSandbox()

    class _RemoteProvider:
        uses_thread_data_mounts = False

        async def acquire_async(self, thread_id: str, *, user_id: str) -> str:
            assert (thread_id, user_id) == ("thread-timeout", "owner-a")
            return "remote-timeout"

        def get(self, sandbox_id: str):
            assert sandbox_id == "remote-timeout"
            return sandbox

    monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", _RemoteProvider)
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test"},
        binding_id="binding-1",
        published_http_client_factory=_published_resource_client_factory(
            _ChunkOnlyAsyncStream([b"complete"]),
            content_length=8,
        ),
    )
    message = InboundMessage(
        channel_name="feishu:binding-1",
        chat_id="chat-1",
        user_id="user-1",
        text="review [file]",
        thread_ts="message-timeout",
        files=[{"file_key": "file-1"}],
    )

    with pytest.raises(TimeoutError, match="sandbox sync"):
        await asyncio.wait_for(
            channel.materialize_published_files(
                message,
                "thread-timeout",
                owner_user_id="owner-a",
                max_input_bytes=1_024,
            ),
            timeout=0.5,
        )
    assert await asyncio.to_thread(sync_started.wait, 1.0)
    release_sync.set()
    assert await asyncio.to_thread(cleanup_finished.wait, 1.0)

    uploads_dir = get_paths().sandbox_uploads_dir("thread-timeout", user_id="owner-a")
    assert list(uploads_dir.iterdir()) == []
    assert remote_files == set()


@pytest.mark.asyncio
async def test_published_feishu_files_use_explicit_owner_paths_and_stream_bytes(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    monkeypatch.setattr(
        "app.channels.feishu.get_sandbox_provider",
        lambda: SimpleNamespace(acquire=lambda _thread_id, *, user_id: "local"),
    )

    for owner, expected in (("owner-a", b"owner-a"), ("owner-b", b"owner-b")):
        stream = _ChunkOnlyAsyncStream([expected])

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "token"}, request=request)
            return httpx.Response(
                200,
                headers={
                    "content-disposition": 'attachment; filename="shared.bin"',
                    "content-length": str(len(expected)),
                },
                stream=stream,
                request=request,
            )

        transport = httpx.MockTransport(handler)
        channel = FeishuChannel(
            MessageBus(),
            {"app_id": "test", "app_secret": "test"},
            published_http_client_factory=lambda: httpx.AsyncClient(transport=transport),
        )
        message = InboundMessage(
            channel_name="feishu:binding-1",
            chat_id="chat-1",
            user_id="user-1",
            text="review [file]",
            thread_ts="message-1",
            files=[{"file_key": f"file-{owner}"}],
        )
        prepared, byte_count = await channel.materialize_published_files(
            message,
            "shared-thread",
            owner_user_id=owner,
            max_input_bytes=1_024,
        )
        actual = get_paths().sandbox_uploads_dir("shared-thread", user_id=owner) / "shared.bin"
        assert actual.read_bytes() == expected
        assert byte_count == len(expected)
        assert prepared.text == "review /mnt/user-data/uploads/shared.bin"

    assert get_paths().sandbox_uploads_dir("shared-thread", user_id="owner-a") != get_paths().sandbox_uploads_dir(
        "shared-thread",
        user_id="owner-b",
    )


@pytest.mark.asyncio
async def test_published_feishu_aggregate_limit_cleans_partial_files_and_accepts_boundary(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    monkeypatch.setattr(
        "app.channels.feishu.get_sandbox_provider",
        lambda: SimpleNamespace(acquire=lambda _thread_id, *, user_id: "local"),
    )

    def new_channel() -> FeishuChannel:
        streams = iter([_ChunkOnlyAsyncStream([b"abc"]), _ChunkOnlyAsyncStream([b"abc"])])

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "token"}, request=request)
            return httpx.Response(
                200,
                headers={
                    "content-disposition": 'attachment; filename="input.bin"',
                    "content-length": "3",
                },
                stream=next(streams),
                request=request,
            )

        transport = httpx.MockTransport(handler)
        return FeishuChannel(
            MessageBus(),
            {"app_id": "test", "app_secret": "test"},
            published_http_client_factory=lambda: httpx.AsyncClient(transport=transport),
        )

    final_text = "compare /mnt/user-data/uploads/input.bin and /mnt/user-data/uploads/input_1.bin"
    files = [{"file_key": "file-1"}, {"file_key": "file-2"}]
    channel = new_channel()
    rejected = InboundMessage(
        channel_name="feishu:binding-1",
        chat_id="chat-1",
        user_id="user-1",
        text="compare [file] and [file]",
        thread_ts="message-1",
        files=files,
    )
    with pytest.raises(ValueError, match="input quota"):
        await channel.materialize_published_files(
            rejected,
            "aggregate-reject",
            owner_user_id="owner-a",
            max_input_bytes=len(final_text.encode("utf-8")) + 5,
        )
    reject_dir = get_paths().sandbox_uploads_dir("aggregate-reject", user_id="owner-a")
    assert list(reject_dir.iterdir()) == []

    channel = new_channel()
    accepted = InboundMessage(
        channel_name="feishu:binding-1",
        chat_id="chat-1",
        user_id="user-1",
        text="compare [file] and [file]",
        thread_ts="message-2",
        files=files,
    )
    prepared, byte_count = await channel.materialize_published_files(
        accepted,
        "aggregate-boundary",
        owner_user_id="owner-a",
        max_input_bytes=len(final_text.encode("utf-8")) + 6,
    )
    assert prepared.text == final_text
    assert byte_count == 6
    assert len(list(get_paths().sandbox_uploads_dir("aggregate-boundary", user_id="owner-a").iterdir())) == 2


def test_feishu_on_message_extracts_image_and_file_keys():
    bus = MessageBus()
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})

    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"

    # Rich text with one image and one file element.
    event.event.message.content = json.dumps(
        {
            "content": [
                [
                    {"tag": "text", "text": "See"},
                    {"tag": "img", "image_key": "img_123"},
                    {"tag": "file", "file_key": "file_456"},
                ]
            ]
        }
    )

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        files = mock_make_inbound.call_args[1]["files"]
        assert files == [{"image_key": "img_123"}, {"file_key": "file_456"}]
        assert "[image]" in mock_make_inbound.call_args[1]["text"]
        assert "[file]" in mock_make_inbound.call_args[1]["text"]


@pytest.mark.parametrize("command", sorted(KNOWN_CHANNEL_COMMANDS))
def test_feishu_recognizes_all_known_slash_commands(command):
    """Every entry in KNOWN_CHANNEL_COMMANDS must be classified as a command."""
    bus = MessageBus()
    config = {"app_id": "test", "app_secret": "test"}
    channel = FeishuChannel(bus, config)

    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"
    event.event.message.content = json.dumps({"text": command})

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        assert mock_make_inbound.call_args[1]["msg_type"].value == "command", f"{command!r} should be classified as COMMAND"


@pytest.mark.parametrize(
    "text",
    [
        "/unknown",
        "/mnt/user-data/outputs/prd/technical-design.md",
        "/etc/passwd",
        "/not-a-command at all",
    ],
)
def test_feishu_treats_unknown_slash_text_as_chat(text):
    """Slash-prefixed text that is not a known command must be classified as CHAT."""
    bus = MessageBus()
    config = {"app_id": "test", "app_secret": "test"}
    channel = FeishuChannel(bus, config)

    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"
    event.event.message.content = json.dumps({"text": text})

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        assert mock_make_inbound.call_args[1]["msg_type"].value == "chat", f"{text!r} should be classified as CHAT"
