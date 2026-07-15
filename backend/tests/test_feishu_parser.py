import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


@pytest.mark.asyncio
async def test_published_feishu_files_use_explicit_owner_paths_and_stream_bytes(
    tmp_path,
    monkeypatch,
):
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    monkeypatch.setattr(
        "app.channels.feishu.get_sandbox_provider",
        lambda: SimpleNamespace(acquire=lambda _thread_id: "local"),
    )

    class _RequestBuilder:
        def message_id(self, _value):
            return self

        def file_key(self, _value):
            return self

        def type(self, _value):
            return self

        def build(self):
            return object()

    class _Request:
        @staticmethod
        def builder():
            return _RequestBuilder()

    class _Response:
        code = 0
        msg = "ok"
        file_name = "shared.bin"

        def __init__(self, content: bytes) -> None:
            self.file = io.BytesIO(content)

        def success(self) -> bool:
            return True

    channel = FeishuChannel(MessageBus(), {"app_id": "test", "app_secret": "test"})
    channel._GetMessageResourceRequest = _Request
    responses = iter([_Response(b"owner-a"), _Response(b"owner-b")])
    channel._api_client = SimpleNamespace(
        im=SimpleNamespace(
            v1=SimpleNamespace(
                message_resource=SimpleNamespace(get=lambda _request: next(responses)),
            )
        )
    )

    for owner, expected in (("owner-a", b"owner-a"), ("owner-b", b"owner-b")):
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
        lambda: SimpleNamespace(acquire=lambda _thread_id: "local"),
    )

    class _RequestBuilder:
        def message_id(self, _value):
            return self

        def file_key(self, _value):
            return self

        def type(self, _value):
            return self

        def build(self):
            return object()

    class _Request:
        builder = staticmethod(_RequestBuilder)

    class _Response:
        code = 0
        msg = "ok"
        file_name = "input.bin"

        def __init__(self) -> None:
            self.file = io.BytesIO(b"abc")

        def success(self) -> bool:
            return True

    channel = FeishuChannel(MessageBus(), {"app_id": "test", "app_secret": "test"})
    channel._GetMessageResourceRequest = _Request

    def reset_responses() -> None:
        responses = iter([_Response(), _Response()])
        channel._api_client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    message_resource=SimpleNamespace(get=lambda _request: next(responses)),
                )
            )
        )

    final_text = "compare /mnt/user-data/uploads/input.bin and /mnt/user-data/uploads/input_1.bin"
    files = [{"file_key": "file-1"}, {"file_key": "file-2"}]
    reset_responses()
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

    reset_responses()
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
