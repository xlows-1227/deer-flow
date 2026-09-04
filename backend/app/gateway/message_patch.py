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

    ``__user`` copy rows (DynamicContextMiddleware appends them to checkpoint
    state) are stripped here so the frontend does not show duplicate human
    messages at the bottom of the list.
    """
    out: list[dict[str, Any]] = []
    skipped_user_copies = 0
    for row in rows:
        drow = dict(row)
        inner = drow.get("content")
        if isinstance(inner, Mapping):
            # Skip __user copies — they duplicate the original human message
            # and appear at the end of the message list, causing UI glitches.
            msg_id = inner.get("id")
            if isinstance(msg_id, str) and msg_id.endswith("__user"):
                skipped_user_copies += 1
                continue
            inner_copy = dict(inner)
            if patch_message_dict(inner_copy):
                drow["content"] = inner_copy
        out.append(drow)
    if skipped_user_copies:
        logger.info("patch_event_rows: skipped %d __user copies", skipped_user_copies)
    return out


def _backfill_message_timestamps(messages: list[Any]) -> None:
    """Backfill missing ``additional_kwargs.timestamp`` for messages in-place.

    Some human/tool messages in checkpoint state lack a timestamp because
    ``ThreadDataMiddleware.before_agent`` only stamps the LAST HumanMessage,
    and ``DynamicContextMiddleware`` runs later splitting it into a hidden
    reminder + visible ``__user`` copy that doesn't inherit the timestamp.

    For each message lacking a timestamp, use the next AI message's timestamp
    as a proxy; if no following AI exists, fall back to the previous AI's
    timestamp. This ensures every message returned by the history endpoint
    carries a timestamp so the frontend merge/dedup logic can order correctly.
    """
    if not isinstance(messages, list) or not messages:
        return

    def _get_ts(msg: Any) -> str | None:
        if not isinstance(msg, Mapping):
            return None
        ak = msg.get("additional_kwargs")
        if not isinstance(ak, Mapping):
            return None
        ts = ak.get("timestamp")
        return ts if isinstance(ts, str) and ts else None

    def _is_ai(msg: Any) -> bool:
        if not isinstance(msg, Mapping):
            return False
        return msg.get("type") in ("ai", "AIMessage", "AIMessageChunk")

    n = len(messages)
    # Pre-compute nearest AI timestamps looking forward and backward.
    next_ts_after: list[str | None] = [None] * n
    prev_ts_before: list[str | None] = [None] * n

    last_seen: str | None = None
    for i in range(n):
        prev_ts_before[i] = last_seen
        if _is_ai(messages[i]):
            ts = _get_ts(messages[i])
            if ts:
                last_seen = ts

    last_seen = None
    for i in range(n - 1, -1, -1):
        next_ts_after[i] = last_seen
        if _is_ai(messages[i]):
            ts = _get_ts(messages[i])
            if ts:
                last_seen = ts

    backfill_types = {"human", "HumanMessage", "ai", "AIMessage", "AIMessageChunk", "tool", "ToolMessage"}
    for idx, msg in enumerate(messages):
        if not isinstance(msg, Mapping):
            continue
        if msg.get("type") not in backfill_types:
            continue
        ak = msg.get("additional_kwargs")
        if not isinstance(ak, Mapping):
            continue
        if ak.get("timestamp"):
            continue
        # Skip __user copies: their position at the end of the message list
        # means _backfill would assign them a wrong timestamp (from a nearby
        # AI message rather than the original human message).  Frontend
        # dedup uses identity matching (stripping the __user suffix) which
        # does not require a timestamp.
        msg_id = msg.get("id") or ""
        if isinstance(msg_id, str) and msg_id.endswith("__user"):
            continue
        chosen = next_ts_after[idx] or prev_ts_before[idx]
        if not chosen:
            continue
        # Copy-on-write to avoid mutating shared dicts referenced elsewhere.
        mcopy = dict(msg)
        ak_copy = dict(ak)
        ak_copy["timestamp"] = chosen
        mcopy["additional_kwargs"] = ak_copy
        messages[idx] = mcopy
        logger.debug(
            "patch_channel_values_messages: backfilled timestamp %s for msg idx=%d id=%r type=%s",
            chosen, idx, msg.get("id"), msg.get("type"),
        )


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
    # Remove __user copies: DynamicContextMiddleware appends a visible
    # ``__user`` copy of each human message to the end of the checkpoint
    # message list.  The original message is already in the correct position,
    # so the __user copy is a duplicate that causes the frontend to show
    # messages twice (once in-position, once at the bottom).  Strip them
    # before returning to the frontend.
    original_len = len(messages)
    # Log all message IDs/types before filtering for debugging
    for i, m in enumerate(messages):
        if isinstance(m, Mapping):
            mid = m.get("id", "NULL")
            mtype = m.get("type", "?")
            ak = m.get("additional_kwargs", {})
            hidden = ak.get("hide_from_ui", False) if isinstance(ak, Mapping) else False
            content_str = str(m.get("content", ""))[:60]
            logger.info(
                "patch_channel_values_messages: msg[%d] id=%r type=%s hidden=%s content=%s",
                i, mid, mtype, hidden, content_str,
            )
    messages[:] = [
        m for m in messages
        if not (
            isinstance(m, Mapping)
            and isinstance(m.get("id"), str)
            and m["id"].endswith("__user")
        )
    ]
    removed = original_len - len(messages)
    if removed:
        logger.info("patch_channel_values_messages: removed %d __user copies (original_len=%d, remaining=%d)", removed, original_len, len(messages))
    _backfill_message_timestamps(messages)
