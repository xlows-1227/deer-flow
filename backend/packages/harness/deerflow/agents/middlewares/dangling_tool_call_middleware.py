"""Middleware to fix dangling tool calls in message history.

A dangling tool call occurs when an AIMessage contains tool_calls but there are
no corresponding ToolMessages in the history (e.g., due to user interruption or
request cancellation). This causes LLM errors due to incomplete message format.

This middleware intercepts the model call to detect and patch such gaps by
inserting synthetic ToolMessages with an error indicator immediately after the
AIMessage that made the tool calls, ensuring correct message ordering.

Uses ``wrap_model_call`` to patch the outgoing model request, and ``before_model``
to persist the same ordering fix into graph state (via ``RemoveMessage``) so
checkpointed history stays valid across runs after SSE disconnect / cancellation.
"""

import json
import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


class DanglingToolCallMiddleware(AgentMiddleware[AgentState]):
    """Inserts placeholder ToolMessages for dangling tool calls before model invocation.

    Scans the message history for AIMessages whose tool_calls lack corresponding
    ToolMessages, and injects synthetic error responses immediately after the
    offending AIMessage so the LLM receives a well-formed conversation.
    """

    @staticmethod
    def _parse_raw_tool_call(raw_tc: object) -> dict | None:
        if not isinstance(raw_tc, dict):
            return None

        function = raw_tc.get("function")
        name = raw_tc.get("name")
        if not name and isinstance(function, dict):
            name = function.get("name")

        args = raw_tc.get("args", {})
        if not args and isinstance(function, dict):
            raw_args = function.get("arguments")
            if isinstance(raw_args, str):
                try:
                    parsed_args = json.loads(raw_args)
                except (TypeError, ValueError, json.JSONDecodeError):
                    parsed_args = {}
                args = parsed_args if isinstance(parsed_args, dict) else {}

        raw_id = raw_tc.get("id")
        return {
            "id": raw_id if isinstance(raw_id, str) and raw_id else None,
            "name": name or "unknown",
            "args": args if isinstance(args, dict) else {},
        }

    @staticmethod
    def _message_tool_calls(msg) -> list[dict]:
        """Return normalized tool calls from structured fields or raw provider payloads.

        LangChain stores malformed provider function calls in ``invalid_tool_calls``.
        They do not execute, but provider adapters may still serialize enough of
        the call id/name back into the next request that strict OpenAI-compatible
        validators expect a matching ToolMessage. Treat them as dangling calls so
        the next model request stays well-formed and the model sees a recoverable
        tool error instead of another provider 400.

        Some providers (e.g. Moonshot) keep canonical ids such as ``read_file:0`` only
        in ``additional_kwargs["tool_calls"]`` while ``tool_calls`` is empty or
        missing ids. Always merge both sources so dangling detection matches what
        the provider adapter will serialize on the next request.
        """
        normalized: list[dict] = []
        seen_ids: set[str] = set()

        structured_tool_calls = list(getattr(msg, "tool_calls", None) or [])
        raw_tool_calls = (getattr(msg, "additional_kwargs", None) or {}).get("tool_calls") or []
        if not isinstance(raw_tool_calls, list):
            raw_tool_calls = []

        for index, tc in enumerate(structured_tool_calls):
            if not isinstance(tc, dict):
                continue

            entry = dict(tc)
            tc_id = entry.get("id")
            if not isinstance(tc_id, str) or not tc_id:
                if index < len(raw_tool_calls):
                    parsed_raw = DanglingToolCallMiddleware._parse_raw_tool_call(raw_tool_calls[index])
                    if parsed_raw and parsed_raw.get("id"):
                        entry["id"] = parsed_raw["id"]
                        tc_id = parsed_raw["id"]
            normalized.append(entry)
            if isinstance(tc_id, str) and tc_id:
                seen_ids.add(tc_id)

        for index, raw_tc in enumerate(raw_tool_calls):
            parsed = DanglingToolCallMiddleware._parse_raw_tool_call(raw_tc)
            if parsed is None:
                continue

            tc_id = parsed.get("id")
            if isinstance(tc_id, str) and tc_id:
                if tc_id in seen_ids:
                    continue
                seen_ids.add(tc_id)
                normalized.append(parsed)
                continue

            if index >= len(structured_tool_calls):
                normalized.append(parsed)

        for invalid_tc in getattr(msg, "invalid_tool_calls", None) or []:
            if not isinstance(invalid_tc, dict):
                continue
            normalized.append(
                {
                    "id": invalid_tc.get("id"),
                    "name": invalid_tc.get("name") or "unknown",
                    "args": {},
                    "invalid": True,
                    "error": invalid_tc.get("error"),
                }
            )

        return normalized

    @staticmethod
    def _synthetic_tool_message_content(tool_call: dict) -> str:
        if tool_call.get("invalid"):
            error = tool_call.get("error")
            if isinstance(error, str) and error:
                return f"[Tool call could not be executed because its arguments were invalid: {error}]"
            return "[Tool call could not be executed because its arguments were invalid.]"
        return "[Tool call was interrupted and did not return a result.]"

    def _build_patched_messages(self, messages: list) -> list | None:
        """Return messages with tool results grouped after their tool-call AIMessage.

        This normalizes model-bound causal order before provider serialization while
        preserving already-valid transcripts unchanged.
        """
        tool_messages_by_id: dict[str, deque[ToolMessage]] = defaultdict(deque)
        for msg in messages:
            if isinstance(msg, ToolMessage):
                tool_messages_by_id[msg.tool_call_id].append(msg)

        tool_call_ids: set[str] = set()
        for msg in messages:
            if getattr(msg, "type", None) != "ai":
                continue
            for tc in self._message_tool_calls(msg):
                tc_id = tc.get("id")
                if tc_id:
                    tool_call_ids.add(tc_id)

        patched: list = []
        patch_count = 0
        for msg in messages:
            if isinstance(msg, ToolMessage) and msg.tool_call_id in tool_call_ids:
                continue

            patched.append(msg)
            if getattr(msg, "type", None) != "ai":
                continue

            for tc in self._message_tool_calls(msg):
                tc_id = tc.get("id")
                if not tc_id:
                    continue

                tool_msg_queue = tool_messages_by_id.get(tc_id)
                existing_tool_msg = tool_msg_queue.popleft() if tool_msg_queue else None
                if existing_tool_msg is not None:
                    patched.append(existing_tool_msg)
                else:
                    patched.append(
                        ToolMessage(
                            content=self._synthetic_tool_message_content(tc),
                            tool_call_id=tc_id,
                            name=tc.get("name", "unknown"),
                            status="error",
                        )
                    )
                    patch_count += 1

        if patched == messages:
            return None

        if patch_count:
            logger.warning(f"Injecting {patch_count} placeholder ToolMessage(s) for dangling tool calls")
        return patched

    def _maybe_persist_patched_state(self, state: AgentState) -> dict | None:
        messages = state.get("messages", [])
        patched = self._build_patched_messages(messages)
        if patched is None:
            return None
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *patched,
            ]
        }

    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._maybe_persist_patched_state(state)

    @override
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._maybe_persist_patched_state(state)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        patched = self._build_patched_messages(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        patched = self._build_patched_messages(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return await handler(request)
