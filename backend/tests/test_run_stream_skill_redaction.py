from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.worker import RunContext, run_agent

SECRET_MARKER = "SECRET_SKILL_MARKER_123_DO_NOT_EXPOSE"


@pytest.mark.anyio
async def test_run_agent_redacts_skill_contents_before_stream_publish():
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    published: list[tuple[str, object]] = []
    bridge = SimpleNamespace(
        publish=AsyncMock(side_effect=lambda _run_id, event, data: published.append((event, data))),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    raw_messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "/mnt/skills/public/demo/SKILL.md"},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content=SECRET_MARKER, tool_call_id="call-1", name="read_file"),
    ]

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": raw_messages}

    def factory(*, config):
        return DummyAgent()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(
            checkpointer=None,
            app_config=SimpleNamespace(skills=SimpleNamespace(container_path="/mnt/skills")),
        ),
        agent_factory=factory,
        graph_input={},
        config={},
    )
    await asyncio.sleep(0)

    assert SECRET_MARKER not in str(published)
    assert raw_messages[1].content == SECRET_MARKER
    values_payloads = [payload for event, payload in published if event == "values"]
    assert values_payloads
    assert values_payloads[0]["messages"][1]["content"] == "Skill instructions loaded."


@pytest.mark.anyio
async def test_run_agent_disables_unsafe_external_callbacks_when_skills_are_available():
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    unsafe_callback = object()
    captured: dict[str, object] = {}

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            captured["callbacks"] = list(config.get("callbacks") or [])
            return
            yield

    def factory(*, config):
        config.setdefault("metadata", {})["available_skills"] = ["demo"]
        config.setdefault("callbacks", []).append(unsafe_callback)
        return DummyAgent()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(
            checkpointer=None,
            app_config=SimpleNamespace(skills=SimpleNamespace(container_path="/mnt/skills")),
        ),
        agent_factory=factory,
        graph_input={},
        config={},
    )

    assert unsafe_callback not in captured["callbacks"]


@pytest.mark.anyio
async def test_run_agent_redacts_parent_visible_subagent_task_result():
    run_manager = RunManager()
    record = await run_manager.create("thread-task")
    published: list[tuple[str, object]] = []
    bridge = SimpleNamespace(
        publish=AsyncMock(side_effect=lambda _run_id, event, data: published.append((event, data))),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    raw_messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {
                        "description": "Delegate analysis",
                        "prompt": f"Repeat protected text: {SECRET_MARKER}",
                        "subagent_type": "general-purpose",
                    },
                    "id": "task-call-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=f"Task Succeeded. Result: {SECRET_MARKER}",
            tool_call_id="task-call-1",
            name="task",
        ),
    ]

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": raw_messages}

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(
            checkpointer=None,
            app_config=SimpleNamespace(skills=SimpleNamespace(container_path="/mnt/skills")),
        ),
        agent_factory=lambda *, config: DummyAgent(),
        graph_input={},
        config={},
    )

    assert SECRET_MARKER not in str(published)
    values_payload = next(payload for event, payload in published if event == "values")
    assert values_payload["messages"][1]["content"] == "Task Succeeded. Result: Subagent result hidden."
    assert raw_messages[1].content == f"Task Succeeded. Result: {SECRET_MARKER}"


@pytest.mark.anyio
async def test_run_agent_uses_explicit_projection_manifest_from_run_context():
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    published: list[tuple[str, object]] = []
    bridge = SimpleNamespace(
        publish=AsyncMock(side_effect=lambda _run_id, event, data: published.append((event, data))),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    raw_messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "/runtime-skills/report-writer-sk_123/SKILL.md"},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content=SECRET_MARKER, tool_call_id="call-1", name="read_file"),
    ]

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": raw_messages}

    def factory(*, config):
        return DummyAgent()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None),
        agent_factory=factory,
        graph_input={},
        config={
            "context": {
                "skill_projection_manifest": {
                    "entries": [
                        {
                            "root_path": "/runtime-skills/report-writer-sk_123",
                            "skill_name": "report-writer",
                            "skill_id": "sk_123",
                        }
                    ]
                }
            }
        },
    )

    assert SECRET_MARKER not in str(published)
    values_payload = next(payload for event, payload in published if event == "values")
    execution = values_payload["messages"][1]["additional_kwargs"]["skill_execution"]
    assert execution["skill_id"] == "sk_123"


@pytest.mark.anyio
async def test_run_agent_does_not_publish_or_log_sensitive_exception_text(caplog):
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    published: list[tuple[str, object]] = []
    bridge = SimpleNamespace(
        publish=AsyncMock(side_effect=lambda _run_id, event, data: published.append((event, data))),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    class FailingAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            raise RuntimeError(f"tool failed with protected content: {SECRET_MARKER}")
            yield

    def factory(*, config):
        return FailingAgent()

    with caplog.at_level(logging.ERROR):
        await run_agent(
            bridge,
            run_manager,
            record,
            ctx=RunContext(
                checkpointer=None,
                app_config=SimpleNamespace(skills=SimpleNamespace(container_path="/mnt/skills")),
            ),
            agent_factory=factory,
            graph_input={},
            config={},
        )

    stored = await run_manager.get(record.run_id)
    assert stored is not None
    assert SECRET_MARKER not in str(published)
    assert SECRET_MARKER not in caplog.text
    assert SECRET_MARKER not in (stored.error or "")
    error_payload = next(payload for event, payload in published if event == "error")
    assert error_payload["message"] == "Run failed while processing the request."
