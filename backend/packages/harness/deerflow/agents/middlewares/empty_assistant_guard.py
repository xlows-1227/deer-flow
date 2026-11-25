"""Reject and skip assistant messages that carry neither text nor tool calls.

A provider can end a stream without emitting any content (zero chunks, or
whitespace that is later stripped).  Persisting that empty ``AIMessage``
poisons the thread: every subsequent request replays it and the provider
rejects the whole payload (e.g. OpenAI-compatible APIs answer
``400 the message at position N with role 'assistant' must not be empty``).

Both assistant-message write paths share this module — the flash-direct
stream in ``runtime/runs/worker.py`` and the agent graph's model wrapper
(``LLMErrorHandlingMiddleware``) — so "actionable assistant message" is
defined exactly once.  The request-side helpers below reuse the same check
so already-poisoned threads can still be sent to the provider.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)

EMPTY_ASSISTANT_MESSAGE_TEXT = (
    "模型本次没有返回任何内容，请重试；若反复出现，请切换模型或联系管理员。"
)

NO_SENDABLE_MESSAGES_TEXT = (
    "当前会话没有可以发送的有效内容（历史中的空回复已被跳过），请重新发送你的问题；"
    "若仍失败，请新建会话。"
)


class EmptyAssistantMessageError(RuntimeError):
    """The model produced neither text nor tool calls, so nothing may be persisted."""


class NoSendableMessagesError(RuntimeError):
    """After dropping empty assistant turns nothing is left to send to the model."""



def ai_message_has_payload(message: Any) -> bool:
    """Return whether *message* is safe to persist as an assistant turn.

    Tool-only messages (empty ``content`` but ``tool_calls`` present) are
    legitimate and must keep flowing; whitespace-only text counts as empty.
    """
    if message is None:
        return False
    if getattr(message, "tool_calls", None):
        return True
    if getattr(message, "tool_call_chunks", None):
        return True
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                if part.strip():
                    return True
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return True
    return False


def drop_unactionable_assistant_messages(
    messages: Sequence[Any],
    *,
    thread_id: str | None,
    origin: str,
) -> list[Any]:
    """Return the messages that may be sent to the provider.

    Empty assistant turns (no text, no tool calls) are skipped so an
    already-poisoned thread keeps working.  This affects the *request only*:
    the checkpoint / stored history is left untouched, so nothing is deleted
    from the conversation — later writes still persist the original list.
    """
    kept: list[Any] = []
    skipped: list[int] = []
    for index, message in enumerate(messages):
        role = getattr(message, "type", None)
        if role == "ai" and not ai_message_has_payload(message):
            skipped.append(index)
            # Never log content: it may contain user data.
            logger.warning(
                "Skipping empty assistant message for LLM request: origin=%s thread_id=%s index=%d role=%s",
                origin,
                thread_id or "-",
                index,
                role,
            )
            continue
        kept.append(message)
    if skipped:
        logger.warning(
            "Skipped %d empty assistant message(s) for LLM request (stored history unchanged): "
            "origin=%s thread_id=%s indices=%s",
            len(skipped),
            origin,
            thread_id or "-",
            skipped,
        )
    return kept


def has_sendable_messages(messages: Sequence[Any]) -> bool:
    """Return whether *messages* contains at least one non-system entry."""
    return any(getattr(message, "type", None) != "system" for message in messages)

