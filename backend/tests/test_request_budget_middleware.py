import logging

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.request_budget_middleware import RequestBudgetMiddleware
from deerflow.config.request_budget_config import RequestBudgetConfig


def _request(messages):
    return ModelRequest(model=None, messages=messages)


def test_short_messages_are_returned_unchanged():
    middleware = RequestBudgetMiddleware(max_request_chars=10_000, keep_head_chars=100, keep_tail_chars=100)
    request = _request([HumanMessage(content="hello"), ToolMessage(content="small", tool_call_id="1")])

    result = middleware.wrap_model_call(request, lambda value: value)

    assert result is request
    assert result.messages[1].content == "small"


def test_longest_tool_result_is_trimmed_with_marker():
    middleware = RequestBudgetMiddleware(max_request_chars=1_000, keep_head_chars=100, keep_tail_chars=100)
    long_content = "H" * 100 + "M" * 5_000 + "T" * 100
    human = HumanMessage(content="find the data")
    tool = ToolMessage(content=long_content, tool_call_id="1")
    ai = AIMessage(content="calling tool")
    request = _request([human, ai, tool])

    result = middleware.wrap_model_call(request, lambda value: value)
    trimmed = result.messages[2]

    assert trimmed.content.startswith("H" * 100)
    assert trimmed.content.endswith("T" * 100)
    assert "characters omitted by the request budget middleware" in trimmed.content
    assert len(trimmed.content) < len(long_content)
    # Non-tool messages and the original request are untouched.
    assert result.messages[0] is human
    assert result.messages[1] is ai
    assert tool.content == long_content


def test_no_tool_results_leaves_request_unchanged(caplog):
    middleware = RequestBudgetMiddleware(max_request_chars=500, keep_head_chars=100, keep_tail_chars=100)
    request = _request([HumanMessage(content="x" * 2_000)])

    with caplog.at_level(logging.WARNING):
        result = middleware.wrap_model_call(request, lambda value: value)

    assert result is request
    assert "has no trimmable tool results" in caplog.text


@pytest.mark.asyncio
async def test_async_wrap_also_trims():
    middleware = RequestBudgetMiddleware(max_request_chars=1_000, keep_head_chars=100, keep_tail_chars=100)
    request = _request([ToolMessage(content="A" * 4_000, tool_call_id="1")])

    async def _passthrough(value):
        return value

    result = await middleware.awrap_model_call(request, _passthrough)

    assert "characters omitted by the request budget middleware" in result.messages[0].content


def test_from_config_copies_thresholds():
    middleware = RequestBudgetMiddleware.from_config(
        RequestBudgetConfig(enabled=True, max_request_chars=12_345, keep_head_chars=300, keep_tail_chars=400)
    )

    assert middleware.max_request_chars == 12_345
    assert middleware.keep_head_chars == 300
    assert middleware.keep_tail_chars == 400


def test_default_max_request_chars_leaves_room_for_one_char_per_token():
    config = RequestBudgetConfig()

    assert config.max_request_chars == 800_000
    # Worst case is 1 char per token, so 800k chars must stay below the
    # deployment's 983,616-token request ceiling.
    assert config.max_request_chars < 983_616
