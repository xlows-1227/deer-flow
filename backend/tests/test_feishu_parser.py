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

        def acquire(self, thread_id: str, *, user_id: str) -> str:
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
    release_sync.set()
    assert await asyncio.to_thread(cleanup_finished.wait, 1.0)

    uploads_dir = get_paths().sandbox_uploads_dir("thread-1", user_id="owner-a")
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
