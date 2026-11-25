"""Trim oversized outbound LLM requests before they reach the provider.

The provider rejects requests above its input-token limit with a 400. A single
large tool result (for example a wide database query) can push the whole
conversation over that limit even though the conversation itself is short.
This middleware measures the serialized request size and replaces the longest
``ToolMessage`` payloads with a head + tail preview plus an explicit omission
marker. Only the current model request is modified; conversation state and
checkpoints are never changed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage

from deerflow.config.request_budget_config import RequestBudgetConfig

logger = logging.getLogger(__name__)

_TRUNCATION_MARKER = "\n\n... [{removed} characters omitted by the request budget middleware; re-run the tool with a narrower scope or read the stored file directly if the missing part is needed] ...\n\n"
_MIN_REMOVABLE_CHARS = 200


class RequestBudgetMiddleware(AgentMiddleware):
    """Keep one model request below a configurable serialized-size budget."""

    def __init__(self, *, max_request_chars: int, keep_head_chars: int, keep_tail_chars: int) -> None:
        super().__init__()
        self.max_request_chars = max_request_chars
        self.keep_head_chars = keep_head_chars
        self.keep_tail_chars = keep_tail_chars

    @classmethod
    def from_config(cls, config: RequestBudgetConfig) -> RequestBudgetMiddleware:
        return cls(
            max_request_chars=config.max_request_chars,
            keep_head_chars=config.keep_head_chars,
            keep_tail_chars=config.keep_tail_chars,
        )

    @staticmethod
    def _message_text(message: Any) -> str:
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if content is None:
            return ""
        try:
            return json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
        except TypeError:
            return str(content)

    @classmethod
    def _request_chars(cls, request: ModelRequest) -> int:
        total = sum(len(cls._message_text(message)) + 8 for message in request.messages)
        if request.system_message is not None:
            total += len(cls._message_text(request.system_message)) + 8
        tools = request.tools or []
        if tools:
            try:
                total += len(json.dumps(tools, ensure_ascii=False, sort_keys=True, default=str))
            except TypeError:
                total += sum(len(str(tool)) for tool in tools)
        return total

    def _bounded_request(self, request: ModelRequest) -> ModelRequest:
        total = self._request_chars(request)
        if total <= self.max_request_chars:
            return request

        messages = list(request.messages)
        head = self.keep_head_chars
        tail = self.keep_tail_chars
        candidates: list[tuple[int, int]] = []
        for index, message in enumerate(messages):
            if not isinstance(message, ToolMessage):
                continue
            length = len(self._message_text(message))
            if length <= head + tail + _MIN_REMOVABLE_CHARS:
                continue
            candidates.append((length, index))
        candidates.sort(reverse=True)

        removed_total = 0
        trimmed = 0
        for length, index in candidates:
            if total - removed_total <= self.max_request_chars:
                break
            text = self._message_text(messages[index])
            removed = length - head - tail
            if removed <= 0:
                continue
            new_content = text[:head] + _TRUNCATION_MARKER.format(removed=removed) + (text[-tail:] if tail else "")
            messages[index] = messages[index].model_copy(update={"content": new_content})
            removed_total += length - len(new_content)
            trimmed += 1

        if not trimmed:
            logger.warning(
                "LLM request size %s chars exceeds budget %s but has no trimmable tool results",
                total,
                self.max_request_chars,
            )
            return request

        logger.warning(
            "Trimmed %d oversized tool result(s) for LLM request budget: ~%s -> ~%s chars (budget %s)",
            trimmed,
            total,
            total - removed_total,
            self.max_request_chars,
        )
        return request.override(messages=messages)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._bounded_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._bounded_request(request))
