"""Tests for TokenUsageMiddleware attribution annotations."""

import importlib
import logging
from dataclasses import dataclass, replace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from deerflow.agents.middlewares.token_usage_middleware import (
    TOKEN_USAGE_ATTRIBUTION_KEY,
    PublishedRunTokenLimitError,
    TokenUsageMiddleware,
)


def _make_runtime():
    runtime = MagicMock()
    runtime.context = {"thread_id": "test-thread"}
    return runtime


class _TokenCountingModel:
    model_fields = {"max_tokens": object()}
    max_tokens = 20

    def get_num_tokens_from_messages(self, messages):
        del messages
        return 3

    def get_num_tokens(self, text):
        return len(text)


class _MessageCountingModel(_TokenCountingModel):
    def get_num_tokens_from_messages(self, messages):
        return len(messages)


class _UnboundedModel:
    model_fields = {}

    def get_num_tokens_from_messages(self, messages):
        del messages
        return 3


@dataclass(frozen=True)
class _ModelRequest:
    model: object
    messages: list
    system_message: object | None = None
    tools: list | None = None
    model_settings: dict | None = None
    runtime: object | None = None

    def override(self, **overrides):
        return replace(self, **overrides)


class TestTokenUsageMiddleware:
    def test_loop_warning_is_counted_before_final_output_cap(self):
        runtime = _make_runtime()
        loop = LoopDetectionMiddleware(warn_threshold=2, hard_limit=10)
        repeated_call = {"name": "bash", "args": {"command": "ls"}}
        for index in range(2):
            loop.after_model(
                {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[{**repeated_call, "id": f"call-{index}"}],
                        )
                    ]
                },
                runtime,
            )
        budget = TokenUsageMiddleware(max_tokens_per_run=10)
        request = _ModelRequest(
            model=_MessageCountingModel(),
            messages=[
                HumanMessage(content="current"),
                AIMessage(content="", tool_calls=[{**repeated_call, "id": "call-1"}]),
                ToolMessage(content="result", tool_call_id="call-1"),
            ],
            tools=[],
            model_settings={},
            runtime=runtime,
        )

        bounded = loop.wrap_model_call(
            request,
            lambda final_request: budget.wrap_model_call(final_request, lambda value: value),
        )

        assert bounded.messages[-1].name == "loop_warning"
        assert bounded.model_settings["max_tokens"] == 6

    def test_published_run_caps_each_model_call_to_remaining_budget(self):
        middleware = TokenUsageMiddleware(max_tokens_per_run=10)
        request = _ModelRequest(
            model=_TokenCountingModel(),
            messages=[
                HumanMessage(content="current"),
                AIMessage(content="step", usage_metadata={"input_tokens": 2, "output_tokens": 2, "total_tokens": 4}),
                ToolMessage(content="result", tool_call_id="call-1"),
            ],
            tools=[],
            model_settings={},
        )

        bounded = middleware.wrap_model_call(request, lambda value: value)

        assert bounded.model_settings["max_tokens"] == 3

    def test_published_run_rejects_models_without_an_output_token_cap(self):
        middleware = TokenUsageMiddleware(max_tokens_per_run=10)
        request = _ModelRequest(
            model=_UnboundedModel(),
            messages=[HumanMessage(content="current")],
            tools=[],
            model_settings={},
        )

        with pytest.raises(PublishedRunTokenLimitError, match="does not support"):
            middleware.wrap_model_call(request, lambda value: value)

    def test_published_run_stops_when_cumulative_usage_exceeds_limit(self):
        middleware = TokenUsageMiddleware(max_tokens_per_run=10)
        messages = [
            AIMessage(content="step", usage_metadata={"input_tokens": 3, "output_tokens": 3, "total_tokens": 6}),
            AIMessage(content="done", usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}),
        ]

        with pytest.raises(PublishedRunTokenLimitError):
            middleware.after_model({"messages": messages}, _make_runtime())

    def test_published_run_does_not_execute_tools_after_consuming_exact_limit(self):
        middleware = TokenUsageMiddleware(max_tokens_per_run=10)
        message = AIMessage(
            content="",
            tool_calls=[{"id": "call-1", "name": "web_search", "args": {"query": "x"}}],
            usage_metadata={"input_tokens": 6, "output_tokens": 4, "total_tokens": 10},
        )

        with pytest.raises(PublishedRunTokenLimitError):
            middleware.after_model({"messages": [message]}, _make_runtime())

    def test_published_run_limit_ignores_previous_conversation_turns(self):
        middleware = TokenUsageMiddleware(max_tokens_per_run=10)
        messages = [
            HumanMessage(content="previous"),
            AIMessage(content="old answer", usage_metadata={"input_tokens": 50, "output_tokens": 50, "total_tokens": 100}),
            HumanMessage(content="current"),
            AIMessage(content="new answer", usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}),
        ]

        result = middleware.after_model({"messages": messages}, _make_runtime())

        assert result is not None

    def test_logs_cache_token_details(self, caplog):
        middleware = TokenUsageMiddleware()
        message = AIMessage(
            content="Here is the final answer.",
            usage_metadata={
                "input_tokens": 350,
                "output_tokens": 240,
                "total_tokens": 590,
                "input_token_details": {
                    "audio": 10,
                    "cache_creation": 200,
                    "cache_read": 100,
                },
                "output_token_details": {
                    "audio": 10,
                    "reasoning": 200,
                },
            },
        )

        with caplog.at_level(
            logging.INFO,
            logger="deerflow.agents.middlewares.token_usage_middleware",
        ):
            result = middleware.after_model({"messages": [message]}, _make_runtime())

        assert result is not None
        assert "LLM token usage: input=350 output=240 total=590" in caplog.text
        assert "input_token_details={'audio': 10, 'cache_creation': 200, 'cache_read': 100}" in caplog.text
        assert "output_token_details={'audio': 10, 'reasoning': 200}" in caplog.text

    def test_logs_basic_tokens_when_no_detail_fields_in_usage_metadata(self, caplog):
        """When usage_metadata has only totals (no input_token_details), log just the counts."""
        middleware = TokenUsageMiddleware()
        message = AIMessage(
            content="Here is the final answer.",
            usage_metadata={
                "input_tokens": 350,
                "output_tokens": 240,
                "total_tokens": 590,
            },
        )

        with caplog.at_level(
            logging.INFO,
            logger="deerflow.agents.middlewares.token_usage_middleware",
        ):
            result = middleware.after_model({"messages": [message]}, _make_runtime())

        assert result is not None
        assert "LLM token usage: input=350 output=240 total=590" in caplog.text
        assert "input_token_details" not in caplog.text

    def test_no_log_when_usage_metadata_is_missing(self, caplog):
        """When usage_metadata is absent, no token usage line is logged."""
        middleware = TokenUsageMiddleware()
        message = AIMessage(
            content="Here is the final answer.",
            response_metadata={
                "usage": {
                    "input_tokens": 350,
                    "output_tokens": 240,
                    "total_tokens": 590,
                }
            },
        )

        with caplog.at_level(
            logging.INFO,
            logger="deerflow.agents.middlewares.token_usage_middleware",
        ):
            result = middleware.after_model({"messages": [message]}, _make_runtime())

        assert result is not None
        assert "LLM token usage" not in caplog.text

    def test_annotates_todo_updates_with_structured_actions(self):
        middleware = TokenUsageMiddleware()
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "write_todos:1",
                    "name": "write_todos",
                    "args": {
                        "todos": [
                            {"content": "Inspect streaming path", "status": "completed"},
                            {"content": "Design token attribution schema", "status": "in_progress"},
                        ]
                    },
                }
            ],
            usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        )

        state = {
            "messages": [message],
            "todos": [
                {"content": "Inspect streaming path", "status": "in_progress"},
                {"content": "Design token attribution schema", "status": "pending"},
            ],
        }

        result = middleware.after_model(state, _make_runtime())

        assert result is not None
        updated_message = result["messages"][0]
        attribution = updated_message.additional_kwargs[TOKEN_USAGE_ATTRIBUTION_KEY]
        assert attribution["kind"] == "tool_batch"
        assert attribution["shared_attribution"] is True
        assert attribution["tool_call_ids"] == ["write_todos:1"]
        assert attribution["actions"] == [
            {
                "kind": "todo_complete",
                "content": "Inspect streaming path",
                "tool_call_id": "write_todos:1",
            },
            {
                "kind": "todo_start",
                "content": "Design token attribution schema",
                "tool_call_id": "write_todos:1",
            },
        ]

    def test_annotates_subagent_and_search_steps(self):
        middleware = TokenUsageMiddleware()
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "task:1",
                    "name": "task",
                    "args": {
                        "description": "spec-coder patch message grouping",
                        "subagent_type": "general-purpose",
                    },
                },
                {
                    "id": "web_search:1",
                    "name": "web_search",
                    "args": {"query": "LangGraph useStream messages tuple"},
                },
            ],
        )

        result = middleware.after_model({"messages": [message]}, _make_runtime())

        assert result is not None
        attribution = result["messages"][0].additional_kwargs[TOKEN_USAGE_ATTRIBUTION_KEY]
        assert attribution["kind"] == "tool_batch"
        assert attribution["shared_attribution"] is True
        assert attribution["actions"] == [
            {
                "kind": "subagent",
                "description": "spec-coder patch message grouping",
                "subagent_type": "general-purpose",
                "tool_call_id": "task:1",
            },
            {
                "kind": "search",
                "tool_name": "web_search",
                "query": "LangGraph useStream messages tuple",
                "tool_call_id": "web_search:1",
            },
        ]

    def test_marks_final_answer_when_no_tools(self):
        middleware = TokenUsageMiddleware()
        message = AIMessage(content="Here is the final answer.")

        result = middleware.after_model({"messages": [message]}, _make_runtime())

        assert result is not None
        attribution = result["messages"][0].additional_kwargs[TOKEN_USAGE_ATTRIBUTION_KEY]
        assert attribution["kind"] == "final_answer"
        assert attribution["shared_attribution"] is False
        assert attribution["actions"] == []

    def test_annotates_removed_todos(self):
        middleware = TokenUsageMiddleware()
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "write_todos:remove",
                    "name": "write_todos",
                    "args": {
                        "todos": [],
                    },
                }
            ],
        )

        result = middleware.after_model(
            {
                "messages": [message],
                "todos": [
                    {"content": "Archive obsolete plan", "status": "pending"},
                ],
            },
            _make_runtime(),
        )

        assert result is not None
        attribution = result["messages"][0].additional_kwargs[TOKEN_USAGE_ATTRIBUTION_KEY]
        assert attribution["kind"] == "todo_update"
        assert attribution["shared_attribution"] is False
        assert attribution["actions"] == [
            {
                "kind": "todo_remove",
                "content": "Archive obsolete plan",
                "tool_call_id": "write_todos:remove",
            }
        ]

    def test_merges_subagent_usage_by_message_position_when_ai_message_ids_are_missing(self, monkeypatch):
        middleware = TokenUsageMiddleware()
        first_dispatch = AIMessage(
            content="",
            tool_calls=[{"id": "task:first", "name": "task", "args": {}}],
        )
        second_dispatch = AIMessage(
            content="",
            tool_calls=[
                {"id": "task:second-a", "name": "task", "args": {}},
                {"id": "task:second-b", "name": "task", "args": {}},
            ],
        )
        messages = [
            first_dispatch,
            ToolMessage(content="first", tool_call_id="task:first"),
            second_dispatch,
            ToolMessage(content="second-a", tool_call_id="task:second-a"),
            ToolMessage(content="second-b", tool_call_id="task:second-b"),
            AIMessage(content="done"),
        ]
        cached_usage = {
            "task:second-a": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            "task:second-b": {"input_tokens": 20, "output_tokens": 7, "total_tokens": 27},
        }

        task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")
        monkeypatch.setattr(
            task_tool_module,
            "pop_cached_subagent_usage",
            lambda tool_call_id: cached_usage.pop(tool_call_id, None),
        )

        result = middleware.after_model({"messages": messages}, _make_runtime())

        assert result is not None
        usage_updates = [message for message in result["messages"] if getattr(message, "usage_metadata", None)]
        assert len(usage_updates) == 1
        updated = usage_updates[0]
        assert updated.tool_calls == second_dispatch.tool_calls
        assert updated.usage_metadata == {
            "input_tokens": 30,
            "output_tokens": 12,
            "total_tokens": 42,
        }
