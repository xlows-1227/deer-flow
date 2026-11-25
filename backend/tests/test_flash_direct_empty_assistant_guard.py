"""Write-side guard: an empty assistant message must never reach the checkpoint."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from deerflow.agents.middlewares.empty_assistant_guard import (
    EMPTY_ASSISTANT_MESSAGE_TEXT,
    EmptyAssistantMessageError,
    NoSendableMessagesError,
    drop_unactionable_assistant_messages,
)
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.worker import (
    RunContext,
    _run_flash_direct_model,
    run_agent,
)


class _FakeModel:
    def __init__(self, chunks: list[AIMessageChunk]) -> None:
        self.chunks = list(chunks)
        self.seen_messages: list | None = None

    def with_config(self, **_kwargs):
        return self

    async def astream(self, messages, config=None):
        self.seen_messages = messages
        for chunk in self.chunks:
            yield chunk


def _fake_app_config() -> SimpleNamespace:
    return SimpleNamespace(
        memory=SimpleNamespace(enabled=False),
        get_model_config=lambda name: SimpleNamespace(name=name),
    )


async def _run_flash(
    chunks: list[AIMessageChunk],
    persist: AsyncMock | None = None,
    *,
    graph_input: dict | None = None,
    pre_run_checkpoint_tuple=None,
):
    """Run the flash-direct path with only the model stream faked."""
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    bridge = SimpleNamespace(publish=AsyncMock())
    redactor = SimpleNamespace(redact_stream_payload=lambda mode, payload, **kw: payload)
    persist = persist or AsyncMock()
    model = _FakeModel(chunks)

    with (
        patch("deerflow.agents.lead_agent.agent._resolve_model_name", return_value="fake-model"),
        patch("deerflow.agents.lead_agent.agent._resolve_available_skill_names", return_value=[]),
        patch("deerflow.agents.lead_agent.prompt.apply_prompt_template", return_value="SYS"),
        patch("deerflow.publishing.runtime_loader.resolve_runtime_agent_config", return_value=None),
        patch("deerflow.publishing.runtime_loader.resolve_runtime_agent_instructions", return_value=""),
        patch("deerflow.models.factory.get_cached_chat_model", return_value=model),
        patch("deerflow.runtime.runs.worker._persist_flash_direct_checkpoint", persist),
        patch("deerflow.runtime.runs.worker._queue_flash_memory_capture"),
    ):
        await _run_flash_direct_model(
            bridge=bridge,
            run_manager=run_manager,
            record=record,
            ctx=RunContext(checkpointer=None, app_config=_fake_app_config()),
            graph_input=graph_input or {"messages": [HumanMessage(content="你好")]},
            config={"configurable": {"model_name": "fake-model"}},
            runnable_config={},
            requested_modes={"values"},
            stream_subgraphs=False,
            checkpointer=None,
            pre_run_checkpoint_tuple=pre_run_checkpoint_tuple,
            redactor=redactor,
        )
    return persist, model


@pytest.mark.anyio
async def test_zero_chunk_response_is_not_persisted_and_fails() -> None:
    persist = AsyncMock()
    with pytest.raises(EmptyAssistantMessageError):
        await _run_flash([], persist=persist)
    persist.assert_not_awaited()


@pytest.mark.anyio
async def test_whitespace_only_response_is_rejected() -> None:
    with pytest.raises(EmptyAssistantMessageError):
        await _run_flash([AIMessageChunk(content="   \n\t ")])


@pytest.mark.anyio
async def test_tool_only_clarification_message_is_still_persisted() -> None:
    """Empty text with tool_calls is legitimate and must keep flowing."""
    chunk = AIMessageChunk(
        content="",
        tool_call_chunks=[
            {
                "name": "ask_clarification",
                "args": '{"question": "请确认要继续吗？"}',
                "id": "call-1",
                "index": 0,
                "type": "tool_call_chunk",
            }
        ],
    )

    persist, _model = await _run_flash([chunk])

    persist.assert_awaited_once()
    messages = persist.await_args.kwargs["channel_values"]["messages"]
    assistant = messages[-2]
    assert assistant.tool_calls and assistant.tool_calls[0]["name"] == "ask_clarification"


@pytest.mark.anyio
async def test_run_agent_marks_run_failed_instead_of_success() -> None:
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    async def _empty_response(**_kwargs):
        raise EmptyAssistantMessageError("empty assistant response (streamed_chunks=0)")

    with (
        patch("deerflow.runtime.runs.worker._should_use_flash_direct_path", return_value=True),
        patch("deerflow.runtime.runs.worker._run_flash_direct_model", _empty_response),
    ):
        await run_agent(
            bridge,
            run_manager,
            record,
            ctx=RunContext(checkpointer=None, app_config=object()),
            agent_factory=lambda **kwargs: None,
            graph_input={"messages": [HumanMessage(content="你好")]},
            config={},
        )

    fetched = await run_manager.get(record.run_id)
    assert fetched is not None
    assert fetched.status == RunStatus.error
    assert fetched.error == EMPTY_ASSISTANT_MESSAGE_TEXT

    error_events = [
        call_args for call_args in bridge.publish.await_args_list if call_args.args[1] == "error"
    ]
    assert error_events, "the client must receive an actionable error event"
    assert error_events[0].args[2]["message"] == EMPTY_ASSISTANT_MESSAGE_TEXT


def test_drop_unactionable_messages_logs_index_without_content(caplog) -> None:
    tool_only = AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {}, "id": "call-1", "type": "tool_call"}],
    )
    messages = [
        HumanMessage(content="机密问题"),
        AIMessage(content=""),
        tool_only,
        AIMessage(content="   \n"),
    ]

    with caplog.at_level(
        logging.WARNING, logger="deerflow.agents.middlewares.empty_assistant_guard"
    ):
        kept = drop_unactionable_assistant_messages(messages, thread_id="t-1", origin="test")

    assert kept == [messages[0], messages[2]]
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "thread_id=t-1" in text
    assert "index=1" in text and "index=3" in text
    assert "role=ai" in text
    assert "indices=[1, 3]" in text
    assert "机密问题" not in text  # content must never be logged


@pytest.mark.anyio
async def test_empty_history_message_is_dropped_from_payload_but_kept_in_history() -> None:
    history = [HumanMessage(content="上一个问题", id="h1"), AIMessage(content="", id="bad")]
    ckpt = SimpleNamespace(checkpoint={"channel_values": {"messages": list(history)}})
    persist = AsyncMock()

    persist, model = await _run_flash(
        [AIMessageChunk(content="新回答")],
        persist=persist,
        pre_run_checkpoint_tuple=ckpt,
    )

    sent = model.seen_messages
    assert sent[0].type == "system"
    assert [m.type for m in sent[1:]] == ["human", "human"]
    assert all(m.content.strip() for m in sent[1:])

    # Persistence still receives the original, unfiltered history: the empty
    # assistant turn is NOT removed from stored history.  (The checkpoint path
    # may still normalise its *content* into a tool-omission marker, which is
    # pre-existing display behaviour unrelated to this request-side filter.)
    persisted = persist.await_args.kwargs["channel_values"]["messages"]
    assert [m.type for m in persisted] == ["human", "ai", "human", "ai"]
    assert persisted[0].id == "h1"
    assert persisted[1].id == "bad"


@pytest.mark.anyio
async def test_no_sendable_messages_raises_instead_of_sending_empty_request() -> None:
    persist = AsyncMock()
    with pytest.raises(NoSendableMessagesError):
        await _run_flash(
            [AIMessageChunk(content="x")],
            persist=persist,
            graph_input={"messages": [AIMessage(content="")]},
        )
    persist.assert_not_awaited()
