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


def _message_timestamp(msg: Mapping[str, Any]) -> str | None:
    """Return a message's ``additional_kwargs.timestamp`` if present."""
    ak = msg.get("additional_kwargs")
    if not isinstance(ak, Mapping):
        return None
    ts = ak.get("timestamp")
    return ts if isinstance(ts, str) and ts else None


def _is_ai_message(msg: Any) -> bool:
    return isinstance(msg, Mapping) and msg.get("type") in ("ai", "AIMessage", "AIMessageChunk")


def restore_stranded_dynamic_context_copies(messages: list) -> None:
    """Move a stranded DynamicContext ``__user`` copy back beside its reminder.

    ``DynamicContextMiddleware`` injects the reminder with an ID-swap: the
    reminder takes the target's id (replacing it in place) while the visible
    ``{id}__user`` copy gets a fresh id, which ``add_messages`` APPENDS at the
    end of the list. On a genuine first turn the target already is the last
    message, so the copy lands immediately after the reminder. But when the
    reminder is deferred to a later turn — e.g. the earlier turns ran through
    the flash-direct path, which bypasses the graph — the conversation's first
    user message is yanked to the very end and stamped with the injection time,
    so it renders in the middle of the conversation with a wrong timestamp.
    Restore the copy to its slot and replace that stale stamp with the nearest
    assistant timestamp (a proxy, mirroring ``_backfill_message_timestamps``).
    """
    reminder_index_by_id: dict[str, int] = {}
    for idx, msg in enumerate(messages):
        if not isinstance(msg, Mapping):
            continue
        mid = msg.get("id")
        ak = msg.get("additional_kwargs")
        if isinstance(mid, str) and isinstance(ak, Mapping) and ak.get("dynamic_context_reminder"):
            reminder_index_by_id[mid] = idx
    if not reminder_index_by_id:
        return

    def _nearest_ai_ts(insert_idx: int) -> str | None:
        for msg in messages[insert_idx:]:
            if _is_ai_message(msg):
                ts = _message_timestamp(msg)
                if ts:
                    return ts
        for msg in reversed(messages[:insert_idx]):
            if _is_ai_message(msg):
                ts = _message_timestamp(msg)
                if ts:
                    return ts
        return None

    stranded_by_base: dict[int, list[Mapping]] = {}
    dropped_indexes: set[int] = set()
    for idx, msg in enumerate(messages):
        if not (isinstance(msg, Mapping) and isinstance(msg.get("id"), str) and msg["id"].endswith("__user")):
            continue
        base_idx = reminder_index_by_id.get(msg["id"][: -len("__user")])
        if base_idx is None or base_idx + 1 == idx:
            continue
        mcopy = dict(msg)
        ak_copy = dict(mcopy.get("additional_kwargs") or {})
        proxy_ts = _nearest_ai_ts(base_idx + 1)
        if proxy_ts:
            ak_copy["timestamp"] = proxy_ts
        mcopy["additional_kwargs"] = ak_copy
        stranded_by_base.setdefault(base_idx, []).append(mcopy)
        dropped_indexes.add(idx)

    if not dropped_indexes:
        return

    ordered: list[Any] = []
    for idx, msg in enumerate(messages):
        if idx in dropped_indexes:
            continue
        ordered.append(msg)
        ordered.extend(stranded_by_base.get(idx, []))
    messages[:] = ordered
    logger.info(
        "restore_stranded_dynamic_context_copies: relocated %d __user copy/copies",
        len(dropped_indexes),
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
    restore_stranded_dynamic_context_copies(messages)
    # Remove __user copies only when they are true duplicates, i.e. another
    # message with the same base id is still present AND visible.  Two data
    # shapes exist:
    #
    # 1. Legacy: DynamicContextMiddleware appended a visible ``__user`` copy
    #    at the END of the list while the visible original stayed in place —
    #    the copy duplicates it and must be stripped.
    # 2. Current (ID-swap split): the message carrying the original id is the
    #    HIDDEN reminder (``hide_from_ui``) and the ``__user`` copy right
    #    after it is the ONLY visible version of the user's message.
    #    Stripping it here made /threads/{id}/history drop the user's
    #    question entirely while /threads/{id}/state kept it, so the frontend
    #    intermittently lost the first Q depending on which response landed
    #    first.
    original_len = len(messages)
    visible_base_ids = {
        m["id"]
        for m in messages
        if isinstance(m, Mapping)
        and isinstance(m.get("id"), str)
        and not m["id"].endswith("__user")
        and not (
            isinstance(m.get("additional_kwargs"), Mapping)
            and m["additional_kwargs"].get("hide_from_ui")
        )
    }
    messages[:] = [
        m for m in messages
        if not (
            isinstance(m, Mapping)
            and isinstance(m.get("id"), str)
            and m["id"].endswith("__user")
            and m["id"][: -len("__user")] in visible_base_ids
        )
    ]
    removed = original_len - len(messages)
    if removed:
        logger.info("patch_channel_values_messages: removed %d __user copies (original_len=%d, remaining=%d)", removed, original_len, len(messages))
    _backfill_message_timestamps(messages)
