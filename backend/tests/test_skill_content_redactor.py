from __future__ import annotations

import copy
import importlib

from langchain_core.messages import AIMessage, ToolMessage

SECRET_MARKER = "SECRET_SKILL_MARKER_123_DO_NOT_EXPOSE"


def _privacy_module():
    return importlib.import_module("deerflow.skills.privacy")


def _dict_messages(
    *,
    name: str = "read_file",
    args: dict | None = None,
    result_name: str | None = None,
) -> list[dict]:
    return [
        {
            "type": "ai",
            "id": "ai-1",
            "content": "",
            "tool_calls": [
                {
                    "name": name,
                    "args": args
                    or {
                        "description": "Load data analysis skill",
                        "path": "/mnt/skills/public/data-analysis/SKILL.md",
                    },
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        },
        {
            "type": "tool",
            "id": "tool-1",
            "name": result_name or name,
            "tool_call_id": "call-1",
            "content": SECRET_MARKER,
            "additional_kwargs": {},
        },
    ]


def test_redacts_skill_call_and_result_without_mutating_input():
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(skills_root="/mnt/skills")
    messages = _dict_messages()
    messages[0]["additional_kwargs"] = {
        "provider_tool_payload": SECRET_MARKER,
    }
    messages[1]["additional_kwargs"] = {
        "provider_tool_payload": SECRET_MARKER,
    }
    messages[1]["response_metadata"] = {"debug": SECRET_MARKER}
    messages[1]["artifact"] = {"raw": SECRET_MARKER}
    original = copy.deepcopy(messages)

    redacted = redactor.redact_messages(messages, run_id="run-1")

    assert messages == original
    assert redacted[0]["tool_calls"][0]["id"] == "call-1"
    assert redacted[0]["tool_calls"][0]["args"] == {
        "description": "Load data analysis skill",
        "skill_name": "data-analysis",
        "category": "public",
        "redacted": True,
    }
    assert SECRET_MARKER not in str(redacted)
    assert redacted[1]["content"] == "Skill instructions loaded."
    assert redacted[1]["additional_kwargs"]["visibility"] == "redacted"
    assert redacted[1]["additional_kwargs"]["skill_execution"]["skill_name"] == "data-analysis"
    assert "provider_tool_payload" not in redacted[0]["additional_kwargs"]
    assert "provider_tool_payload" not in redacted[1]["additional_kwargs"]
    assert redacted[1]["response_metadata"] == {}
    assert redacted[1]["artifact"] is None


def test_preserves_non_skill_file_reads_when_call_is_present():
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(skills_root="/mnt/skills")
    messages = _dict_messages(
        args={
            "description": "Read user report",
            "path": "/mnt/user-data/workspace/report.md",
        }
    )

    redacted = redactor.redact_messages(messages, run_id="run-1")

    assert redacted == messages


def test_redacts_orphan_read_file_result_conservatively():
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(skills_root="/mnt/skills")
    message = {
        "type": "tool",
        "name": "read_file",
        "tool_call_id": "orphan-call",
        "content": SECRET_MARKER,
        "additional_kwargs": {},
    }

    redacted = redactor.redact_message(message, run_id="run-1")

    assert SECRET_MARKER not in str(redacted)
    assert redacted["additional_kwargs"]["visibility"] == "redacted"


def test_normalizes_skill_paths_and_rejects_parent_escape():
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(skills_root="/mnt/skills")
    skill_messages = _dict_messages(
        args={
            "description": "Load helper",
            "path": "\\mnt\\skills\\custom\\report-writer\\references\\guide.md",
        }
    )
    escaped_messages = _dict_messages(
        args={
            "description": "Read workspace file",
            "path": "/mnt/skills/../user-data/workspace/report.md",
        }
    )

    redacted_skill = redactor.redact_messages(skill_messages, run_id="run-skill")
    redacted_escape = redactor.redact_messages(escaped_messages, run_id="run-escape")

    assert SECRET_MARKER not in str(redacted_skill)
    assert redacted_skill[1]["additional_kwargs"]["skill_execution"]["skill_name"] == "report-writer"
    assert redacted_escape == escaped_messages


def test_redacts_langchain_messages_and_preserves_types():
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(skills_root="/mnt/skills")
    messages = [
        AIMessage(
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
        ),
        ToolMessage(content=SECRET_MARKER, tool_call_id="call-1", name="read_file"),
    ]

    redacted = redactor.redact_messages(messages, run_id="run-1")

    assert isinstance(redacted[0], AIMessage)
    assert isinstance(redacted[1], ToolMessage)
    assert messages[1].content == SECRET_MARKER
    assert redacted[1].content == "Skill instructions loaded."


def test_redacts_nested_stream_payloads_and_bash_skill_access():
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(skills_root="/mnt/skills")
    messages = _dict_messages(
        name="bash",
        args={
            "description": "Inspect skill helper",
            "command": "python /mnt/skills/public/data-analysis/scripts/check.py",
        },
    )
    payload = {"agent": {"messages": messages}, "safe_value": 7}

    redacted = redactor.redact_stream_payload("updates", payload, run_id="run-1")

    assert SECRET_MARKER not in str(redacted)
    assert redacted["safe_value"] == 7
    assert payload["agent"]["messages"][1]["content"] == SECRET_MARKER


def test_redacts_event_batches_and_persists_safe_metadata():
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(skills_root="/mnt/skills")
    messages = _dict_messages()
    events = [
        {
            "run_id": "run-1",
            "event_type": "llm.ai.response",
            "category": "message",
            "content": messages[0],
            "metadata": {},
        },
        {
            "run_id": "run-1",
            "event_type": "llm.tool.result",
            "category": "message",
            "content": messages[1],
            "metadata": {"caller": "lead_agent", "unsafe": SECRET_MARKER},
        },
    ]

    redacted = redactor.redact_event_batch(events)

    assert SECRET_MARKER not in str(redacted)
    assert events[1]["content"]["content"] == SECRET_MARKER
    assert redacted[1]["metadata"]["skill_execution"]["skill_name"] == "data-analysis"
    assert redacted[1]["metadata"]["caller"] == "lead_agent"


def test_projection_entries_override_configured_root_for_future_run_grants():
    privacy = _privacy_module()
    projection = privacy.SkillProjectionEntry(
        root_path="/runtime-skills/report-writer-sk_123",
        descriptor=privacy.SkillExecutionDescriptor(
            skill_name="report-writer",
            category="custom",
            skill_id="sk_123",
            skill_handle="report-writer-sk_123",
            version_seq=3,
        ),
    )
    redactor = privacy.SkillContentRedactor(
        skills_root="/mnt/skills",
        projections=[projection],
    )
    messages = _dict_messages(
        args={
            "description": "Load report writer",
            "path": "/runtime-skills/report-writer-sk_123/SKILL.md",
        }
    )

    redacted = redactor.redact_messages(messages, run_id="run-1")

    execution = redacted[1]["additional_kwargs"]["skill_execution"]
    assert execution["skill_id"] == "sk_123"
    assert execution["version_seq"] == 3
    assert SECRET_MARKER not in str(redacted)


def test_builds_projection_entries_from_explicit_run_context():
    privacy = _privacy_module()
    app_config = type(
        "Config",
        (),
        {"skills": type("Skills", (), {"container_path": "/mnt/skills"})()},
    )()
    runtime_context = {
        "skill_projection_manifest": {
            "entries": [
                {
                    "root_path": "/runtime-skills/report-writer-sk_123",
                    "skill_name": "report-writer",
                    "category": "custom",
                    "skill_id": "sk_123",
                    "skill_handle": "report-writer-sk_123",
                    "version_seq": 3,
                }
            ]
        }
    }
    redactor = privacy.SkillContentRedactor.from_run_context(
        app_config=app_config,
        runtime_context=runtime_context,
    )
    messages = _dict_messages(
        args={
            "path": "/runtime-skills/report-writer-sk_123/references/guide.md",
        }
    )

    redacted = redactor.redact_messages(messages, run_id="run-1")

    execution = redacted[1]["additional_kwargs"]["skill_execution"]
    assert execution["skill_id"] == "sk_123"
    assert execution["skill_handle"] == "report-writer-sk_123"
    assert SECRET_MARKER not in str(redacted)


def test_builds_projection_entries_from_run_grants_with_projection_root():
    privacy = _privacy_module()
    runtime_context = {
        "skill_grants": [
            {
                "projection_root": "/runtime-skills/data-analysis-sk_456",
                "skill_name": "data-analysis",
                "skill_id": "sk_456",
                "version_seq": 7,
            }
        ]
    }
    redactor = privacy.SkillContentRedactor.from_run_context(
        app_config=None,
        runtime_context=runtime_context,
    )
    messages = _dict_messages(args={"path": "/runtime-skills/data-analysis-sk_456/SKILL.md"})

    redacted = redactor.redact_messages(messages, run_id="run-1")

    execution = redacted[1]["additional_kwargs"]["skill_execution"]
    assert execution["skill_id"] == "sk_456"
    assert execution["version_seq"] == 7


def test_subgraph_namespaces_isolate_reused_tool_call_ids():
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(skills_root="/mnt/skills")
    sensitive_call = _dict_messages()[0]
    ordinary_call = _dict_messages(args={"path": "/mnt/user-data/workspace/report.md"})[0]
    sensitive_result = _dict_messages()[1]
    ordinary_result = _dict_messages()[1]

    redactor.observe_message(sensitive_call, run_id="run-1", namespace="subagent:a")
    redactor.observe_message(ordinary_call, run_id="run-1", namespace="subagent:b")
    safe_a = redactor.redact_message(
        sensitive_result,
        run_id="run-1",
        namespace="subagent:a",
    )
    safe_b = redactor.redact_message(
        ordinary_result,
        run_id="run-1",
        namespace="subagent:b",
    )

    assert safe_a["content"] == "Skill instructions loaded."
    assert safe_b["content"] == SECRET_MARKER


def test_redacts_parent_visible_subagent_task_result_and_preserves_status():
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(skills_root="/mnt/skills")
    messages = [
        {
            "type": "ai",
            "content": "",
            "tool_calls": [
                {
                    "name": "task",
                    "args": {
                        "description": "Research with a subagent",
                        "prompt": f"Use private instructions: {SECRET_MARKER}",
                        "subagent_type": "general-purpose",
                    },
                    "id": "task-call-1",
                    "type": "tool_call",
                }
            ],
        },
        {
            "type": "tool",
            "name": "task",
            "tool_call_id": "task-call-1",
            "content": f"Task Succeeded. Result: {SECRET_MARKER}",
            "additional_kwargs": {"raw_subagent_trace": SECRET_MARKER},
        },
    ]
    original = copy.deepcopy(messages)

    redacted = redactor.redact_messages(messages, run_id="run-task")

    assert messages == original
    assert SECRET_MARKER not in str(redacted)
    assert redacted[0]["tool_calls"][0]["args"] == {
        "description": "Research with a subagent",
        "subagent_type": "general-purpose",
        "redacted": True,
    }
    assert redacted[1]["content"] == "Task Succeeded. Result: Subagent result hidden."
    assert redacted[1]["additional_kwargs"] == {
        "visibility": "redacted",
        "event_type": "subagent_execution",
        "subagent_execution": {
            "status": "completed",
            "summary": "Subagent result hidden",
        },
    }


def test_redacts_subagent_task_event_metadata():
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(skills_root="/mnt/skills")
    event = {
        "run_id": "run-task",
        "event_type": "tool.end",
        "category": "message",
        "content": {
            "type": "tool",
            "name": "task",
            "tool_call_id": "task-call-1",
            "content": f"Task Succeeded. Result: {SECRET_MARKER}",
        },
        "metadata": {
            "caller": "lead_agent",
            "unsafe_subagent_result": SECRET_MARKER,
        },
    }

    redacted = redactor.redact_event(event)

    assert SECRET_MARKER not in str(redacted)
    assert redacted["metadata"] == {
        "caller": "lead_agent",
        "subagent_execution": {
            "status": "completed",
            "summary": "Subagent result hidden",
        },
    }


def test_redaction_failure_returns_safe_error_payload(monkeypatch, caplog):
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(skills_root="/mnt/skills", boundary="stream")
    before = privacy.get_skill_redaction_metrics_snapshot()

    def _raise(*_args, **_kwargs):
        raise RuntimeError(SECRET_MARKER)

    monkeypatch.setattr(redactor, "_redact_nested", _raise)

    redacted = redactor.redact_stream_payload(
        "future-unknown-mode",
        {"messages": _dict_messages()},
        run_id="run-1",
    )

    assert redacted == {
        "redaction_error": True,
        "message": "Sensitive tool payload hidden.",
    }
    assert SECRET_MARKER not in str(redacted)
    assert SECRET_MARKER not in caplog.text
    after = privacy.get_skill_redaction_metrics_snapshot()
    assert after["skill_redaction_fail_closed_total"][("stream", "other")] == before["skill_redaction_fail_closed_total"].get(("stream", "other"), 0) + 1
    assert after["skill_redaction_errors_total"][("stream", "other")] == before["skill_redaction_errors_total"].get(("stream", "other"), 0) + 1


def test_redaction_metrics_use_low_cardinality_labels():
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(
        skills_root="/mnt/skills",
        boundary="gateway messages with unsafe user suffix",
    )
    before = privacy.get_skill_redaction_metrics_snapshot()

    redactor.redact_messages(_dict_messages(), run_id="run-with-user-controlled-id")

    after = privacy.get_skill_redaction_metrics_snapshot()
    labels = ("other", "read_file")
    assert after["skill_redaction_events_total"][labels] == before["skill_redaction_events_total"].get(labels, 0) + 1


def test_redacts_trace_error_text_after_skill_access():
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(skills_root="/mnt/skills")
    messages = _dict_messages()
    events = [
        {
            "run_id": "run-1",
            "event_type": "llm.ai.response",
            "category": "message",
            "content": messages[0],
            "metadata": {"caller": "lead_agent"},
        },
        {
            "run_id": "run-1",
            "event_type": "llm.error",
            "category": "trace",
            "content": f"provider echoed {SECRET_MARKER}",
            "metadata": {"caller": "lead_agent", "unsafe": SECRET_MARKER},
        },
    ]

    redacted = redactor.redact_event_batch(events)

    assert SECRET_MARKER not in str(redacted)
    assert redacted[1]["content"] == "Sensitive execution details hidden."
    assert redacted[1]["metadata"] == {"caller": "lead_agent"}


def test_redacts_trace_error_text_after_subagent_task():
    privacy = _privacy_module()
    redactor = privacy.SkillContentRedactor(skills_root="/mnt/skills")
    events = [
        {
            "run_id": "run-task",
            "event_type": "llm.ai.response",
            "category": "message",
            "content": {
                "type": "ai",
                "content": "",
                "tool_calls": [
                    {
                        "name": "task",
                        "args": {"prompt": "delegate work"},
                        "id": "task-call-1",
                        "type": "tool_call",
                    }
                ],
            },
            "metadata": {"caller": "lead_agent"},
        },
        {
            "run_id": "run-task",
            "event_type": "llm.error",
            "category": "trace",
            "content": f"subagent provider echoed {SECRET_MARKER}",
            "metadata": {"caller": "lead_agent", "unsafe": SECRET_MARKER},
        },
    ]

    redacted = redactor.redact_event_batch(events)

    assert SECRET_MARKER not in str(redacted)
    assert redacted[1]["content"] == "Sensitive execution details hidden."
    assert redacted[1]["metadata"] == {"caller": "lead_agent"}
