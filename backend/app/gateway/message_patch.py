"""Helpers that back-fill AI-message content with human-readable tool-call markers.

When a model emits tool calls, the streamed AIMessageChunk content is usually
replaced by the generic ``[工具调用已省略]`` placeholder during downstream
cleaning.  The real tool names live inside the associated ``tool_calls`` array
and are only present after LangChain/LangGraph merges a final AI message.

To avoid forcing every consumer to read the nested ``tool_calls`` structure, we
patch the display content string in-place before returning messages from the
REST layer.  That way the frontend banner logic (``detectToolOmissions``) can
translate the generic placeholder into the per-tool badges with real names.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)


_TOOL_OMISSION_MARKER = "[工具调用已省略]"


def _marker_from_names(names: list[str]) -> str:
    return "[工具调用: " + ", ".join(names) + "]"


def _extract_tool_names(obj: Mapping[str, Any]) -> list[str]:
    """Return tool names from a message dict (AIMessage/AIMessageChunk)."""
    names: list[str] = []
    tcs = obj.get("tool_calls")
    if isinstance(tcs, list):
        for tc in tcs:
            name = tc.get("name") if isinstance(tc, Mapping) else None
            if isinstance(name, str) and name and name != "task" and name not in names:
                names.append(name)
    tccs = obj.get("tool_call_chunks")
    if isinstance(tccs, list):
        for tcc in tccs:
            name = tcc.get("name") if isinstance(tcc, Mapping) else None
            if isinstance(name, str) and name and name != "task" and name not in names:
                names.append(name)
    return names


def patch_message_dict(msg: dict[str, Any]) -> bool:
    """Patch a single serialized LangChain message dict in-place.

    Two things are done for AI messages:
    1. (If tool calls are present) inject a human-readable ``[工具调用: …]``
       banner so the frontend displays the real tool names.
    2. Apply English-space restoration to the textual content so glued
       model output such as "Hereissometext" shows correctly with spaces.

    Returns ``True`` if ``msg["content"]`` was changed.
    """
    if not isinstance(msg, Mapping):
        return False
    msg_type = msg.get("type")
    if msg_type not in ("ai", "AIMessage", "AIMessageChunk"):
        return False
    changed = False

    # --- Space restoration for AI text --------------------------------
    content_field = msg.get("content")
    if isinstance(content_field, str) and content_field:
        try:
            from deerflow.runtime.runs.worker import _restore_english_spaces

            restored = _restore_english_spaces(content_field)
            if restored != content_field:
                msg["content"] = restored
                changed = True
        except Exception:
            logger.debug("patch_message_dict: space restoration skipped", exc_info=True)

    # --- Tool-call display name banner ---------------------------------
    names = _extract_tool_names(msg)
    if names:
        content_field2 = msg.get("content")
        if not isinstance(content_field2, str):
            return changed
        target = _marker_from_names(names)
        if target in content_field2:
            return changed
        stripped = content_field2.strip()
        if not stripped or stripped == _TOOL_OMISSION_MARKER:
            msg["content"] = target
            return True
        # Preserve any textual answer; prepend so detectToolOmissions() still parses it.
        msg["content"] = target + "\n" + content_field2
        changed = True
    return changed


def patch_event_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Patch event-store rows (the format ``/runs/{id}/messages`` returns).

    Each row has the shape ``{"event_type": "...", "content": {...}, ...}`` where
    ``content`` holds the serialized LangChain message for AI / tool events.
    Returns a new list (rows copied to dicts) with AI-message content patched
    when it carries tool calls with a missing display marker.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        drow = dict(row)
        inner = drow.get("content")
        if isinstance(inner, Mapping):
            inner_copy = dict(inner)
            if patch_message_dict(inner_copy):
                drow["content"] = inner_copy
        out.append(drow)
    return out


def patch_channel_values_messages(channel_values: Mapping[str, Any]) -> None:
    """Patch ``channel_values["messages"]`` in-place (used by ``/threads/{id}/history``)."""
    if not isinstance(channel_values, Mapping):
        return
    messages = channel_values.get("messages")
    if not isinstance(messages, list):
        return
    for idx, msg in enumerate(messages):
        if isinstance(msg, Mapping):
            mcopy = dict(msg)
            if patch_message_dict(mcopy):
                messages[idx] = mcopy
