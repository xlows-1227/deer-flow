from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.skills.privacy import SkillContentRedactor

SECRET_MARKER = "SECRET_SKILL_MARKER_123_DO_NOT_EXPOSE"


@pytest.mark.anyio
async def test_run_journal_persists_redacted_skill_messages_but_keeps_inputs_raw():
    store = MemoryRunEventStore()
    redactor = SkillContentRedactor(skills_root="/mnt/skills")
    journal = RunJournal("run-1", "thread-1", store, redactor=redactor, flush_threshold=100)
    ai_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "args": {
                    "description": "Load data analysis skill",
                    "path": "/mnt/skills/public/data-analysis/SKILL.md",
                },
                "id": "call-1",
                "type": "tool_call",
            }
        ],
    )
    tool_message = ToolMessage(
        content=SECRET_MARKER,
        tool_call_id="call-1",
        name="read_file",
    )
    response = SimpleNamespace(generations=[[SimpleNamespace(message=ai_message)]])

    journal.on_llm_end(response, run_id=uuid4(), tags=["lead_agent"])
    journal.on_tool_end(tool_message, run_id=uuid4())
    await journal.flush()

    messages = await store.list_messages("thread-1")
    assert SECRET_MARKER not in str(messages)
    assert messages[0]["content"]["tool_calls"][0]["args"]["redacted"] is True
    assert messages[1]["content"]["content"] == "Skill instructions loaded."
    assert messages[1]["metadata"]["skill_execution"]["skill_name"] == "data-analysis"
    assert tool_message.content == SECRET_MARKER


@pytest.mark.anyio
async def test_run_journal_redacts_skill_messages_nested_in_chain_outputs():
    store = MemoryRunEventStore()
    redactor = SkillContentRedactor(skills_root="/mnt/skills")
    journal = RunJournal("run-1", "thread-1", store, redactor=redactor, flush_threshold=100)
    messages = [
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

    journal.on_chain_end({"messages": messages}, run_id=uuid4())
    await journal.flush()

    events = await store.list_events("thread-1", "run-1")
    assert SECRET_MARKER not in str(events)
    assert messages[1].content == SECRET_MARKER
