from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from deerflow.runtime import compaction_service
from deerflow.runtime.compaction_service import compact_thread_checkpoint


class _FakeCheckpointer:
    def __init__(self) -> None:
        self.checkpoint = {"channel_values": {"messages": [HumanMessage(content="old")]}}
        self.metadata = {"created_at": "2026-06-01T00:00:00+00:00"}
        self.put_calls: list[tuple] = []

    async def aget_tuple(self, config):
        return SimpleNamespace(checkpoint=self.checkpoint, metadata=self.metadata)

    async def aput(self, *args):
        self.put_calls.append(args)
        return {"configurable": {"checkpoint_id": "new"}}


class _FakeEventStore:
    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    async def put_batch(self, events):
        self.batches.append(events)
        return events


class _FakeMiddleware:
    def __init__(self) -> None:
        self.runtime_context = None

    async def abefore_model(self, state, runtime):
        self.runtime_context = runtime.context
        runtime.context["__run_journal"].record_middleware(
            tag="compaction",
            name="FakeMiddleware",
            hook="before_model",
            action="summarize",
            changes={"summary": "compressed", "compacted_message_ids": ["old"]},
        )
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                HumanMessage(content="Here is a summary", name="summary"),
                HumanMessage(content="keep"),
            ]
        }


@pytest.mark.anyio
async def test_manual_compaction_flushes_event_after_checkpoint_write(monkeypatch):
    fake_middleware = _FakeMiddleware()
    monkeypatch.setattr(compaction_service, "get_app_config", lambda: SimpleNamespace())
    monkeypatch.setattr(compaction_service, "_create_summarization_middleware", lambda app_config=None: fake_middleware)
    checkpointer = _FakeCheckpointer()
    event_store = _FakeEventStore()

    values = await compact_thread_checkpoint(
        checkpointer=checkpointer,
        thread_id="thread-1",
        custom_instructions="Focus on changed files",
        event_store=event_store,
        run_id="run-1",
    )

    assert fake_middleware.runtime_context["force_compact"] is True
    assert fake_middleware.runtime_context["compact_instructions"] == "Focus on changed files"
    assert len(checkpointer.put_calls) == 1
    assert values["messages"][0]["name"] == "summary"
    assert event_store.batches[0][0]["run_id"] == "run-1"
    assert event_store.batches[0][0]["event_type"] == "middleware:compaction"
    assert event_store.batches[0][0]["content"]["changes"]["summary"] == "compressed"
