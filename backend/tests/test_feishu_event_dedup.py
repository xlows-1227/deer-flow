from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channels.feishu import FeishuChannel, _sdk_event_to_dict
from app.channels.message_bus import MessageBus
from deerflow.persistence.agent_channel.model import AgentChannelRow
from deerflow.persistence.base import Base
from deerflow.persistence.channel_mapping import (
    SYSTEM_CHANNEL_MAPPING_SCOPE,
    ChannelEventRepository,
    MappingScopeConflictError,
)
from deerflow.persistence.published_agent.model import PublishedAgentRow


def _event(
    *,
    event_id: str = "event-1",
    created_at: float | None = None,
    token: str = "verification-token",
):
    created_at = created_at if created_at is not None else time.time()
    return SimpleNamespace(
        header=SimpleNamespace(
            event_id=event_id,
            create_time=str(int(created_at * 1000)),
            token=token,
        ),
        event=SimpleNamespace(
            message=SimpleNamespace(
                chat_id="chat-1",
                message_id="message-1",
                root_id=None,
                thread_id=None,
                chat_type="p2p",
                content=json.dumps({"text": "hello"}),
            ),
            sender=SimpleNamespace(
                sender_id=SimpleNamespace(open_id="user-1"),
            ),
        ),
    )


@pytest_asyncio.fixture
async def event_repository(tmp_path):
    database_path = tmp_path / "channel-events.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path.as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                PublishedAgentRow(
                    id="agent-1",
                    owner_user_id="owner-1",
                    slug="agent-one",
                    display_name="Agent One",
                    status="published",
                ),
                AgentChannelRow(
                    id="binding-1",
                    agent_id="agent-1",
                    app_id="app-1",
                    secret_ref="secret-1",
                    status="active",
                ),
                AgentChannelRow(
                    id="binding-2",
                    agent_id="agent-1",
                    app_id="app-2",
                    secret_ref="secret-2",
                    status="inactive",
                ),
            ]
        )
        await session.commit()
    repository = ChannelEventRepository(session_factory)
    yield repository
    await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_event_is_dropped_before_bus_dispatch(event_repository: ChannelEventRepository) -> None:
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        event_deduplicator=event_repository,
        verification_token="verification-token",
    )
    channel._main_loop = asyncio.get_running_loop()

    channel._on_message(_event())
    channel._on_message(_event())
    await asyncio.sleep(0.1)

    assert bus.inbound_queue.qsize() == 1
    inbound = await bus.get_inbound()
    assert inbound.metadata["event_id"] == "event-1"
    assert inbound.metadata["binding_id"] == "binding-1"


@pytest.mark.asyncio
async def test_event_id_is_isolated_by_binding(event_repository: ChannelEventRepository) -> None:
    assert await event_repository.claim("binding-1", "event-1", system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE) is True
    assert await event_repository.claim("binding-1", "event-1", system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE) is False
    assert await event_repository.claim("binding-2", "event-1", system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE) is True


@pytest.mark.asyncio
async def test_event_claim_requires_system_scope_and_persisted_binding(event_repository: ChannelEventRepository) -> None:
    with pytest.raises(TypeError, match="system_scope"):
        await event_repository.claim("binding-1", "event-2")

    with pytest.raises(PermissionError, match="system channel mapping scope required"):
        await event_repository.claim("binding-1", "event-2", system_scope=object())

    with pytest.raises(MappingScopeConflictError, match="valid Feishu binding"):
        await event_repository.claim("forged-binding", "event-2", system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE)


@pytest.mark.asyncio
async def test_concurrent_event_claim_has_one_winner(event_repository: ChannelEventRepository) -> None:
    outcomes = await asyncio.gather(
        *(
            event_repository.claim(
                "binding-1",
                "event-concurrent",
                system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE,
            )
            for _ in range(4)
        )
    )

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 3


def test_tampered_verification_token_is_rejected_before_dispatch() -> None:
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        verification_token="verification-token",
    )
    channel._make_inbound = MagicMock()

    channel._on_message(_event(token="tampered-token"))

    channel._make_inbound.assert_not_called()
    assert bus.inbound_queue.empty()


def _card_action_event(
    *,
    event_id: str = "card-event-1",
    created_at: float | None = None,
    body_token: str | None = "verification-token",
    header_token: str | None = None,
    create_time_factory: Callable[[float], str] | None = None,
    message_id: str = "card-message-1",
    value: dict[str, Any] | None = None,
    form_value: dict[str, Any] | None = None,
):
    """Mimic P2CardActionTrigger over the long connection: token lives in the body."""
    created_at = created_at if created_at is not None else time.time()
    if create_time_factory is None:
        create_time_factory = lambda ts: str(int(ts * 1000))  # noqa: E731
    action = SimpleNamespace(
        tag="button",
        value=value if value is not None else {"action": "approve", "record_id": "rec-1"},
    )
    if form_value is not None:
        action.form_value = form_value
    return SimpleNamespace(
        header=SimpleNamespace(
            event_id=event_id,
            create_time=create_time_factory(created_at),
            token=header_token,
        ),
        event=SimpleNamespace(
            token=body_token,
            operator=SimpleNamespace(open_id="user-1"),
            context=SimpleNamespace(open_chat_id="chat-1", open_message_id=message_id),
            action=action,
        ),
    )


def test_stale_timestamp_is_rejected_before_dispatch() -> None:
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        verification_token="verification-token",
    )
    channel._make_inbound = MagicMock()

    channel._on_message(_event(created_at=1_000.0))

    channel._make_inbound.assert_not_called()
    assert bus.inbound_queue.empty()


@pytest.mark.asyncio
async def test_card_action_with_body_token_is_accepted_over_long_connection(
    event_repository: ChannelEventRepository,
) -> None:
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        event_deduplicator=event_repository,
        verification_token="verification-token",
    )
    channel._main_loop = asyncio.get_running_loop()

    response = channel._on_card_action(_card_action_event())

    assert response is not None
    await asyncio.sleep(0.1)
    assert bus.inbound_queue.qsize() == 1
    inbound = await bus.get_inbound()
    assert inbound.metadata["card_action"] is True
    assert inbound.metadata["event_id"] == "card-event-1"
    # 每张审批卡独占一个会话线程（topic=卡片消息 id），并发卡片互不冲突
    assert inbound.topic_id == "card-message-1"
    assert '"action": "approve"' in inbound.text
    assert '"record_id": "rec-1"' in inbound.text


def test_card_action_with_tampered_body_token_is_rejected() -> None:
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        verification_token="verification-token",
    )
    channel._make_inbound = MagicMock()

    channel._on_card_action(_card_action_event(body_token="tampered-token"))

    channel._make_inbound.assert_not_called()
    assert bus.inbound_queue.empty()


def test_card_action_without_any_token_is_accepted_over_long_connection() -> None:
    """新应用的长连接事件可能不携带任何 token：信任已认证连接，直接放行。"""
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        verification_token="verification-token",
    )
    channel._make_inbound = MagicMock()

    channel._on_card_action(_card_action_event(body_token=None, header_token=None))

    channel._make_inbound.assert_called_once()


@pytest.mark.asyncio
async def test_message_event_without_token_is_accepted_over_long_connection(
    event_repository: ChannelEventRepository,
) -> None:
    """新应用长连接订阅的 im.message.receive_v1 事件头无 token（生产实测），需放行。"""
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        event_deduplicator=event_repository,
        verification_token="verification-token",
    )
    channel._main_loop = asyncio.get_running_loop()
    channel._make_inbound = MagicMock(wraps=channel._make_inbound)

    channel._on_message(_event(event_id="event-msg-1", token=None))

    channel._make_inbound.assert_called_once()
    await asyncio.sleep(0.1)
    assert bus.inbound_queue.qsize() == 1


def test_message_event_with_wrong_token_is_still_rejected() -> None:
    """token 出现但与绑定凭据都不匹配（跨应用/配错）时仍须拒绝。"""
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        verification_token="verification-token",
    )
    channel._make_inbound = MagicMock()

    channel._on_message(_event(event_id="event-msg-2", token="wrong-token"))

    channel._make_inbound.assert_not_called()
    assert bus.inbound_queue.empty()


def test_card_action_with_microsecond_timestamp_is_accepted() -> None:
    """card.action.trigger frames arrive with a microsecond create_time."""
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        verification_token="verification-token",
    )
    channel._make_inbound = MagicMock()

    response = channel._on_card_action(_card_action_event(create_time_factory=lambda created_at: str(int(created_at * 1_000_000))))

    assert response is not None
    channel._make_inbound.assert_called_once()


def test_card_action_with_encrypt_key_body_token_is_accepted() -> None:
    """card.action.trigger body token is derived from the app's encrypt key."""
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        verification_token="verification-token",
        encrypt_key="encrypt-key",
    )
    channel._make_inbound = MagicMock()

    response = channel._on_card_action(_card_action_event(body_token="encrypt-key", header_token=None))

    assert response is not None
    channel._make_inbound.assert_called_once()


def test_card_action_with_header_verification_token_is_accepted() -> None:
    """A header token matching the verification token suffices when the body
    token carries unrelated callback material."""
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        verification_token="verification-token",
        encrypt_key="encrypt-key",
    )
    channel._make_inbound = MagicMock()

    response = channel._on_card_action(_card_action_event(body_token="unknown-callback-token", header_token="verification-token"))

    assert response is not None
    channel._make_inbound.assert_called_once()


def test_card_action_with_foreign_tokens_is_rejected() -> None:
    """Neither body nor header token matching any binding credential fails closed."""
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        verification_token="verification-token",
        encrypt_key="encrypt-key",
    )
    channel._make_inbound = MagicMock()

    channel._on_card_action(_card_action_event(body_token="foreign", header_token="also-foreign"))

    channel._make_inbound.assert_not_called()
    assert bus.inbound_queue.empty()


@pytest.mark.asyncio
async def test_card_action_reject_without_reason_is_blocked_and_retriable(
    event_repository: ChannelEventRepository,
) -> None:
    """拒绝必须填写理由：无理由时报错且卡片不登记去重，可填写后重新点击。"""
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        event_deduplicator=event_repository,
        verification_token="verification-token",
    )
    channel._main_loop = asyncio.get_running_loop()

    response = channel._on_card_action(
        _card_action_event(
            value={"action": "reject", "record_id": "rec-1"},
            form_value={"reject_reason": "   "},
        )
    )

    assert response is not None
    assert response.toast.type == "error"
    assert response.toast.content == "请先填写拒绝原因，再点击拒绝按钮"
    await asyncio.sleep(0.1)
    assert bus.inbound_queue.empty()
    assert "card-message-1" not in channel._processed_card_message_ids


@pytest.mark.asyncio
async def test_card_action_reject_with_reason_publishes_with_card_topic(
    event_repository: ChannelEventRepository,
) -> None:
    """带理由的拒绝正常发布，且使用独立卡片 topic 与其它并发审批卡隔离。"""
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        event_deduplicator=event_repository,
        verification_token="verification-token",
    )
    channel._main_loop = asyncio.get_running_loop()

    response = channel._on_card_action(
        _card_action_event(
            message_id="card-message-2",
            value={"action": "reject", "record_id": "rec-2"},
            form_value={"reject_reason": "预算不足"},
        )
    )

    assert response is not None
    assert response.toast.type == "info"
    assert response.toast.content == "❌ 已提交拒绝，正在处理…"
    await asyncio.sleep(0.1)
    assert bus.inbound_queue.qsize() == 1
    inbound = await bus.get_inbound()
    assert inbound.topic_id == "card-message-2"
    assert '"action": "reject"' in inbound.text
    assert "预算不足" in inbound.text


def test_sdk_event_to_dict_serializes_real_card_action_model() -> None:
    """Raw production frames must survive JSON logging verbatim."""
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger

    payload = {
        "schema": "2.0",
        "header": {
            "event_id": "card-event-raw",
            "event_type": "card.action.trigger",
            "create_time": str(int(time.time() * 1_000_000)),
            "token": "header-token",
            "tenant_key": "tenant-key",
            "app_id": "app-id",
        },
        "event": {
            "operator": {"open_id": "user-1"},
            "token": "body-token",
            "action": {"tag": "button", "value": {"action": "approve", "record_id": "rec-1"}},
            "context": {"open_chat_id": "chat-1", "open_message_id": "card-message-1"},
        },
    }

    dumped = _sdk_event_to_dict(P2CardActionTrigger(payload))

    assert json.dumps(dumped, ensure_ascii=False)  # JSON-safe
    assert dumped["header"]["create_time"] == payload["header"]["create_time"]
    assert dumped["header"]["token"] == "header-token"
    assert dumped["event"]["token"] == "body-token"
    assert dumped["event"]["action"]["value"]["record_id"] == "rec-1"


def test_patch_card_after_action_builds_sdk_compatible_request() -> None:
    """The patch request must fit lark-oapi 1.7.x: content-only request body."""
    from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
    )
    captured: dict[str, PatchMessageRequestBody] = {}

    class FakeMessageApi:
        def patch(self, req: Any) -> Any:
            captured["body"] = req.request_body
            return SimpleNamespace(success=lambda: True, code=0, msg="")

    class FakeImV1:
        message = FakeMessageApi()

    class FakeIm:
        v1 = FakeImV1()

    class FakeApiClient:
        im = FakeIm()

    channel._api_client = FakeApiClient()
    channel._PatchMessageRequest = PatchMessageRequest
    channel._PatchMessageRequestBody = PatchMessageRequestBody

    channel._patch_card_after_action("om-card-1", "reject", "预算不足")

    body = captured["body"]
    assert getattr(body, "content_type", None) is None  # field does not exist on this SDK
    card = json.loads(body.content)
    assert card["schema"] == "2.0"
    assert card["header"]["title"]["content"] == "审批已拒绝"
    assert "预算不足" in card["body"]["elements"][0]["content"]


def _make_card_patch_channel(*, get_ok: bool = True) -> tuple[FeishuChannel, list[dict[str, Any]], list[str]]:
    """带 Fake SDK 的通道：记录每次卡片 patch 内容与 get 抓取的原始内容。"""
    from lark_oapi.api.im.v1 import GetMessageRequest, PatchMessageRequest, PatchMessageRequestBody

    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        app_id="app-id",
        app_secret="app-secret",
        binding_id="binding-1",
        agent_id="agent-1",
        verification_token="verification-token",
    )

    original_card = json.dumps(
        {
            "schema": "2.0",
            "header": {"title": {"tag": "plain_text", "content": "审批请求"}, "template": "blue"},
            "body": {"elements": [{"tag": "action", "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "同意"}}]}]},
        },
        ensure_ascii=False,
    )
    patches: list[dict[str, Any]] = []
    gets: list[str] = []

    class FakeMessageApi:
        def patch(self, req: Any) -> Any:
            patches.append({"message_id": req.message_id, "content": req.request_body.content})
            return SimpleNamespace(success=lambda: True, code=0, msg="")

        def get(self, req: Any) -> Any:
            gets.append(req.message_id)
            if not get_ok:
                return SimpleNamespace(success=lambda: False, code=230002, msg="no scope", data=None)
            return SimpleNamespace(
                success=lambda: True,
                code=0,
                msg="",
                data=SimpleNamespace(items=[SimpleNamespace(body=SimpleNamespace(content=original_card))]),
            )

    class FakeImV1:
        message = FakeMessageApi()

    class FakeIm:
        v1 = FakeImV1()

    class FakeApiClient:
        im = FakeIm()

    channel._api_client = FakeApiClient()
    channel._PatchMessageRequest = PatchMessageRequest
    channel._PatchMessageRequestBody = PatchMessageRequestBody
    channel._GetMessageRequest = GetMessageRequest
    channel.send = AsyncMock()  # type: ignore[method-assign]
    return channel, patches, gets


async def _wait_for(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


@pytest.mark.asyncio
async def test_card_click_patches_processing_then_final_on_success(event_repository: ChannelEventRepository) -> None:
    """两阶段：点击打"处理中"（非终态），Run 成功终态 outbound 到达后才打"已通过"。"""
    channel, patches, gets = _make_card_patch_channel()
    channel._event_deduplicator = event_repository
    channel._main_loop = asyncio.get_running_loop()

    response = channel._on_card_action(_card_action_event(value={"action": "approve", "record_id": "rec-1"}))
    assert response.toast.type == "info"

    await _wait_for(lambda: len(patches) >= 1)
    first_card = json.loads(patches[0]["content"])
    assert first_card["header"]["title"]["content"] == "审批处理中"
    assert gets == ["card-message-1"]
    # 终态未到：不重复点击提示为"处理中"
    repeat = channel._on_card_action(_card_action_event(value={"action": "approve", "record_id": "rec-1"}))
    assert repeat.toast.content == "该操作正在处理中，请稍候"

    from app.channels.message_bus import OutboundMessage

    await channel._on_outbound(
        OutboundMessage(
            channel_name=channel.name,
            chat_id="chat-1",
            thread_id="thread-1",
            text="审批完成",
            thread_ts="card-message-1",
            is_final=True,
            metadata={},
        )
    )
    await _wait_for(lambda: len(patches) >= 2)

    final_card = json.loads(patches[-1]["content"])
    assert final_card["header"]["title"]["content"] == "审批已通过"
    # 成功后登记保留（卡片已是终态，禁止再次点击）
    assert "card-message-1" in channel._processed_card_message_ids
    assert "card-message-1" not in channel._pending_card_runs
    channel.send.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_card_run_failure_restores_original_and_clears_dedup(event_repository: ChannelEventRepository) -> None:
    """Run 失败（busy/内部错误等 error 标记终态）：还原原卡片按钮并清除去重，允许重试。"""
    channel, patches, _ = _make_card_patch_channel()
    channel._event_deduplicator = event_repository
    channel._main_loop = asyncio.get_running_loop()

    channel._on_card_action(_card_action_event(value={"action": "approve", "record_id": "rec-1"}))
    await _wait_for(lambda: len(patches) >= 1)

    from app.channels.message_bus import OutboundMessage

    # 进度消息不打终态
    await channel._on_outbound(
        OutboundMessage(
            channel_name=channel.name,
            chat_id="chat-1",
            thread_id="thread-1",
            text="处理中进度",
            thread_ts="card-message-1",
            is_final=False,
            metadata={},
        )
    )
    await asyncio.sleep(0.1)
    assert len(patches) == 1
    assert "card-message-1" in channel._pending_card_runs

    await channel._on_outbound(
        OutboundMessage(
            channel_name=channel.name,
            chat_id="chat-1",
            thread_id="thread-1",
            text="This agent is busy. Please try again later.",
            thread_ts="card-message-1",
            is_final=True,
            metadata={"error": True},
        )
    )
    await _wait_for(lambda: len(patches) >= 2)

    # 还原为原始卡片 JSON（按钮回来了）
    assert json.loads(patches[-1]["content"])["header"]["title"]["content"] == "审批请求"
    assert "card-message-1" not in channel._processed_card_message_ids
    assert "card-message-1" not in channel._pending_card_runs


@pytest.mark.asyncio
async def test_second_card_click_while_first_pending_is_rejected(event_repository: ChannelEventRepository) -> None:
    """并发预检：第一张卡在途时点第二张卡直接拒绝，卡片不动、不登记，可稍后再点。"""
    channel, patches, _ = _make_card_patch_channel()
    channel._event_deduplicator = event_repository
    channel._main_loop = asyncio.get_running_loop()

    channel._on_card_action(_card_action_event(value={"action": "approve", "record_id": "rec-1"}))
    await _wait_for(lambda: len(patches) >= 1)  # 第一张卡进入"处理中"
    await channel.bus.get_inbound()  # 排空第一条（第一张卡的）inbound

    second = channel._on_card_action(
        _card_action_event(
            event_id="card-event-2",
            message_id="card-message-2",
            value={"action": "approve", "record_id": "rec-2"},
        )
    )
    assert second.toast.type == "error"
    assert second.toast.content == "已有审批正在处理，请稍后再试"
    assert channel.bus.inbound_queue.empty()
    assert "card-message-2" not in channel._processed_card_message_ids
    assert "card-message-2" not in channel._pending_card_runs
    await asyncio.sleep(0.1)
    assert all(patch["message_id"] == "card-message-1" for patch in patches)

    # 第一张卡终态后，第二张卡恢复可点击
    from app.channels.message_bus import OutboundMessage

    await channel._on_outbound(
        OutboundMessage(
            channel_name=channel.name,
            chat_id="chat-1",
            thread_id="thread-1",
            text="done",
            thread_ts="card-message-1",
            is_final=True,
            metadata={},
        )
    )
    await _wait_for(lambda: "card-message-1" not in channel._pending_card_runs)
    assert channel._pending_card_runs == {}


@pytest.mark.asyncio
async def test_card_without_original_fetch_leaves_card_untouched_on_failure(event_repository: ChannelEventRepository) -> None:
    """抓不到原始卡片（如缺读消息权限）时不打"处理中"：失败后卡片天然保持原样。"""
    channel, patches, gets = _make_card_patch_channel(get_ok=False)
    channel._event_deduplicator = event_repository
    channel._main_loop = asyncio.get_running_loop()

    channel._on_card_action(_card_action_event(value={"action": "approve", "record_id": "rec-1"}))
    await _wait_for(lambda: len(gets) == 1)
    await asyncio.sleep(0.2)
    assert patches == []  # 未打"处理中"，卡片保持原样

    from app.channels.message_bus import OutboundMessage

    await channel._on_outbound(
        OutboundMessage(
            channel_name=channel.name,
            chat_id="chat-1",
            thread_id="thread-1",
            text="This agent is busy. Please try again later.",
            thread_ts="card-message-1",
            is_final=True,
            metadata={"error": True},
        )
    )
    await _wait_for(lambda: "card-message-1" not in channel._pending_card_runs)
    await asyncio.sleep(0.1)
    assert patches == []  # 失败也无需还原
    assert "card-message-1" not in channel._processed_card_message_ids  # 可重新点击


@pytest.mark.asyncio
async def test_duplicate_card_action_event_leaves_card_untouched(event_repository: ChannelEventRepository) -> None:
    """持久化去重命中（进程重启后事件重放）：事件被丢弃，卡片不打"处理中"且登记回滚。"""
    channel, patches, _ = _make_card_patch_channel()
    channel._event_deduplicator = event_repository
    channel._main_loop = asyncio.get_running_loop()

    # 预先占用持久化事件（模拟重启前已处理过同一事件）
    assert await event_repository.claim("binding-1", "card-event-1", system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE)

    channel._on_card_action(_card_action_event(value={"action": "approve", "record_id": "rec-1"}))

    await _wait_for(lambda: "card-message-1" not in channel._pending_card_runs)
    await asyncio.sleep(0.2)
    assert patches == []  # 卡片完全未被改动
    assert "card-message-1" not in channel._processed_card_message_ids
    assert channel.bus.inbound_queue.empty()
