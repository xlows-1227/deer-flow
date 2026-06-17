"""Convert Mini-Agent messages into LangGraph-compatible message dicts.

deer-flow stores messages inside the LangGraph checkpoint ``messages`` channel.
When the frontend reads them back it runs each entry through
``serialize_lc_object`` (see ``deerflow.runtime.serialization``), which for a
plain ``dict`` returns it unchanged. Therefore the most robust wire format is
the **LangChain message dict representation** — the same shape you get from
``BaseMessage.model_dump()``. That is what this module produces.

Mapping (Mini-Agent → LangChain/LangGraph):

    role "user"      -> {"type": "human",    "content": ...}
    role "assistant" -> {"type": "ai",       "content": ...,
                         "tool_calls": [...], "additional_kwargs": {...}}
    role "tool"      -> {"type": "tool",     "content": ...,
                         "tool_call_id": ..., "name": ...}
    role "system"    -> dropped (deer-flow injects its own system prompt)

Mini-Agent's ``ToolCall`` is ``{"id", "type": "function", "function":
{"name", "arguments": {...}}}`` (the OpenAI wire shape). LangChain's
``ToolCall`` is ``{"name", "args", "id", "type": "tool_call"}`` — note the
flattened ``name``/``args`` and the required ``id``. The conversion below
preserves the id so that the following ``tool`` message can reference it.
"""

from __future__ import annotations

import uuid
from typing import Any

from log_parser import MiniMessage, ParsedSession

# Each converted message gets a stable-ish id so LangGraph's add_messages
# reducer (if it ever runs over these) can dedupe. We only need uniqueness
# within a thread.
_ID_PREFIX = "mig-"


def _new_id(role: str, idx: int) -> str:
    # LangChain uses a UUID per message; we emit deterministic-ish ids so
    # re-running the migration over the same source is idempotent-ish.
    return f"{_ID_PREFIX}{role}-{idx:04d}-{uuid.uuid4().hex[:8]}"


def _content_to_str(content: Any) -> str:
    """Flatten Mini-Agent content (str | list[dict]) into a plain string.

    Assistant/user content can be a list of content blocks. We keep it simple
    and human-readable: concatenate every block's ``text`` field.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                txt = block.get("text") or block.get("content")
                if txt:
                    parts.append(str(txt))
                else:
                    parts.append(str(block))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _convert_tool_calls(mini_calls: list | None) -> list[dict]:
    """OpenAI-style tool_calls -> LangChain ToolCall dicts."""
    if not mini_calls:
        return []
    converted: list[dict] = []
    for call in mini_calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        call_id = call.get("id") or _new_id("tc", len(converted))
        converted.append(
            {
                "name": fn.get("name", "unknown"),
                "args": fn.get("arguments") or {},
                "id": call_id,
                "type": "tool_call",
            }
        )
    return converted


def convert_message(msg: MiniMessage, idx: int) -> dict | None:
    """Convert one Mini-Agent message to a LangGraph message dict.

    Returns ``None`` for messages that should be skipped (system messages,
    empty no-ops).
    """
    role = msg.role
    if role == "system":
        # deer-flow manages its own system prompt at runtime; importing the
        # source one would collide and bloat the context. Drop it.
        return None

    content = _content_to_str(msg.content)

    if role == "user":
        # Skip synthetic summary markers only if they are empty.
        if not content.strip():
            return None
        return {
            "type": "human",
            "content": content,
            "id": _new_id("human", idx),
        }

    if role == "assistant":
        tool_calls = _convert_tool_calls(msg.tool_calls)
        additional_kwargs: dict[str, Any] = {}
        # Preserve extended thinking for models that produced it.
        if msg.thinking:
            additional_kwargs["reasoning"] = msg.thinking
        # An assistant turn with neither text nor tool calls is useless noise.
        if not content.strip() and not tool_calls:
            return None
        out: dict[str, Any] = {
            "type": "ai",
            "content": content,
            "id": _new_id("ai", idx),
        }
        if tool_calls:
            out["tool_calls"] = tool_calls
        if additional_kwargs:
            out["additional_kwargs"] = additional_kwargs
        return out

    if role == "tool":
        # A tool message must reference the assistant tool_call that produced
        # it. Mini-Agent stores that id on the message as ``tool_call_id``.
        tool_call_id = msg.tool_call_id or _new_id("tool", idx)
        out = {
            "type": "tool",
            "content": content,
            "tool_call_id": tool_call_id,
            "id": _new_id("tool", idx),
        }
        if msg.name:
            out["name"] = msg.name
        return out

    # Unknown role — fall back to a human turn so nothing is silently lost.
    return {
        "type": "human",
        "content": f"[unknown role {role!r}] {content}",
        "id": _new_id("unknown", idx),
    }


def derive_title(session: ParsedSession, max_len: int = 60) -> str:
    """Build a human-readable thread title from the first user message."""
    text = session.first_user_text()
    if not text:
        name = session.source_file.stem
        if session.started_at:
            name = session.started_at.strftime("%Y-%m-%d %H:%M")
        return f"Mini-Agent session ({name})"
    # Collapse whitespace and newlines for a clean single-line title.
    flat = " ".join(text.split())
    if len(flat) > max_len:
        flat = flat[: max_len - 1].rstrip() + "…"
    return flat


def convert_session(session: ParsedSession) -> tuple[list[dict], str]:
    """Convert a full parsed session.

    Returns ``(messages, title)`` where ``messages`` is the list of LangGraph
    message dicts (system messages dropped) and ``title`` is the thread title.
    Also repairs tool/tool_call id linkage so deer-flow can render the
    conversation without dangling references.
    """
    raw: list[dict] = []
    for idx, msg in enumerate(session.messages):
        converted = convert_message(msg, idx)
        if converted is not None:
            raw.append(converted)

    _repair_tool_call_ids(raw)
    title = derive_title(session)
    return raw, title


def _repair_tool_call_ids(messages: list[dict]) -> None:
    """Ensure every ``tool`` message references a real assistant tool_call id.

    Mini-Agent's logs sometimes omit ``tool_call_id`` on tool messages (e.g.
    older runs). When that happens we link the tool message to the nearest
    preceding assistant message's *first* tool_call. This keeps LangGraph's
    invariant — a ToolMessage always follows an AIMessage that declared the
    call — satisfied so the chat UI can pair them up.
    """
    pending_tool_call_ids: list[str] = []
    for msg in messages:
        if msg["type"] == "ai":
            tcs = msg.get("tool_calls") or []
            pending_tool_call_ids = [tc["id"] for tc in tcs if tc.get("id")]
        elif msg["type"] == "tool":
            tcid = msg.get("tool_call_id")
            if not tcid or tcid.startswith(_ID_PREFIX + "tool"):
                # Missing or synthetic id — bind to a pending call if any.
                if pending_tool_call_ids:
                    msg["tool_call_id"] = pending_tool_call_ids.pop(0)
                # else: leave as-is; nothing better we can do.
