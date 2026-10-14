"""Middleware for converting text-embedded tool calls to standard tool_calls.

Some LLM providers (e.g. YUMC) return function calls as text content
instead of in the standard ``tool_calls`` response field. This middleware
detects known patterns in the response content and converts them to proper
``tool_calls`` so downstream middlewares can intercept them normally.

Parser patterns are organized per-tool in the ``text_parsers`` subpackage:

- ``text_parsers/ask_clarification.py`` — ask_clarification formats
- ``text_parsers/web_search.py`` — web_search formats
- ``text_parsers/generic.py`` — fallback for any ``tool_name(args)``

Extensibility:
    Add a new per-tool file in ``text_parsers/`` and register it in
    ``text_parsers/__init__.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, override
from uuid import uuid4

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage

from .text_parsers import ALL_PARSERS, TextToolCallParser

logger = logging.getLogger(__name__)


class ToolCallTextParserMiddleware(AgentMiddleware):
    """Converts text-embedded tool calls to standard ``tool_calls``.

    Placed before ``ClarificationMiddleware`` in the middleware chain so
    that converted tool calls are indistinguishable from native ones to
    all downstream processing (tool call interception, token attribution,
    loop detection, etc.).
    """

    state_schema = AgentState

    def __init__(
        self,
        parsers: list[TextToolCallParser] | None = None,
    ) -> None:
        self._parsers = list(parsers) if parsers is not None else list(ALL_PARSERS)
        logger.debug(
            "ToolCallTextParserMiddleware initialized with parsers: %s",
            [p.description for p in self._parsers],
        )

    def _try_parse(self, content: str) -> dict[str, Any] | None:
        """Match content against registered parsers.

        Returns a ``tool_call`` dict if matched, ``None`` otherwise.
        """
        for parser in self._parsers:
            match = parser.pattern.search(content)
            if match:
                try:
                    args = parser.parse(match)
                    tool_name = parser.get_tool_name(match)
                    if not tool_name:
                        continue
                    return {
                        "name": tool_name,
                        "args": args,
                        "id": f"text_tc_{uuid4().hex[:12]}",
                    }
                except Exception:
                    logger.warning(
                        "Failed to parse text tool call for %s",
                        parser.description,
                        exc_info=True,
                    )
        return None

    def _process_response(self, response: ModelCallResult) -> ModelCallResult:
        """Inspect model response and convert text tool calls if needed."""
        if not isinstance(response, AIMessage):
            return response

        content = getattr(response, "content", "")
        if not isinstance(content, str) or not content.strip():
            return response

        existing = getattr(response, "tool_calls", None)

        if existing:
            # Model returned proper tool_calls — but some providers also
            # include a text description of the tool call in content.
            # Strip any matching tool-call text patterns from content.
            cleaned = content
            for parser in self._parsers:
                cleaned = parser.pattern.sub("", cleaned)
            cleaned = cleaned.strip()
            if cleaned != content:
                logger.debug(
                    "Stripped duplicate tool-call text from content "
                    "(existing tool_calls=%d, original_len=%d, cleaned_len=%d)",
                    len(existing),
                    len(content),
                    len(cleaned),
                )
                return response.model_copy(update={"content": cleaned})
            return response

        # No standard tool_calls — try to parse text-embedded ones.
        tool_call, cleaned_content = self._try_parse_with_cleanup(content)
        if tool_call is None:
            return response

        logger.info(
            "Converted text tool call to standard tool_call: %s (args keys: %s)",
            tool_call["name"],
            list(tool_call.get("args", {}).keys())[:5],
        )

        return response.model_copy(
            update={
                "tool_calls": [tool_call],
                "content": cleaned_content,
            },
        )

    def _try_parse_with_cleanup(
        self, content: str
    ) -> tuple[dict[str, Any] | None, str]:
        """Parse tool call and return cleaned content with matched text removed."""
        for parser in self._parsers:
            match = parser.pattern.search(content)
            if match:
                try:
                    args = parser.parse(match)
                    tool_name = parser.get_tool_name(match)
                    if not tool_name:
                        continue
                    # Remove the matched tool_call invocation from content
                    # so frontend doesn't render raw tool_call text.
                    cleaned = content[: match.start()] + content[match.end():]
                    cleaned = cleaned.strip()
                    return (
                        {
                            "name": tool_name,
                            "args": args,
                            "id": f"text_tc_{uuid4().hex[:12]}",
                        },
                        cleaned,
                    )
                except Exception:
                    logger.warning(
                        "Failed to parse text tool call for %s",
                        parser.description,
                        exc_info=True,
                    )
        return None, content

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        response = handler(request)
        return self._process_response(response)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        response = await handler(request)
        return self._process_response(response)


# Module-level helper for flash-direct path (no middleware chain).
_default_middleware = None


def process_text_tool_calls(response):
    """Apply text tool call parsing to a model response.

    Used by flash-direct path which bypasses the agent graph middleware chain.
    """
    global _default_middleware
    if _default_middleware is None:
        _default_middleware = ToolCallTextParserMiddleware()
    return _default_middleware._process_response(response)
