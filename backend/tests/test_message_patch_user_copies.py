"""Tests for ``__user`` copy handling in ``patch_channel_values_messages``.

``DynamicContextMiddleware`` splits each user turn into a hidden reminder
(keeping the original id, ``hide_from_ui: true``) plus a visible copy whose id
ends with ``__user``.  The history endpoint used to strip every ``__user``
message unconditionally, which deleted the user's question from
``/threads/{id}/history`` while ``/threads/{id}/state`` kept it — the frontend
then intermittently lost the first question depending on which response landed
first.

These tests pin down the two data shapes:

1. Current (ID-swap split): the base-id message is the hidden reminder, so the
   ``__user`` copy is the ONLY visible version and must be KEPT.
2. Legacy: the original visible message stays in place and a duplicate
   ``__user`` copy is appended at the end — the copy must be stripped.
"""

from __future__ import annotations

import copy

from app.gateway.message_patch import patch_channel_values_messages

# Realistic message shapes mirroring the DynamicContextMiddleware ID-swap.
BASE_ID = "97a3acf8-2dbd-4be6-bd9b-15c94ca20c38"
USER_COPY_ID = BASE_ID + "__user"


def _hidden_reminder(content: str = "搜索点新闻") -> dict:
    return {
        "id": BASE_ID,
        "type": "human",
        "content": content,
        "additional_kwargs": {"hide_from_ui": True, "timestamp": "2026-08-01T10:00:00"},
    }


def _user_copy(content: str = "搜索点新闻") -> dict:
    return {
        "id": USER_COPY_ID,
        "type": "human",
        "content": content,
        "name": "user-input",
        "additional_kwargs": {},
    }


def _ai_reply(content: str = "好的") -> dict:
    return {
        "id": "ai-1",
        "type": "ai",
        "content": content,
        "additional_kwargs": {"timestamp": "2026-08-01T10:00:05"},
    }


def _ids(messages: list[dict]) -> list[str]:
    return [m["id"] for m in messages]


def test_id_swap_user_copy_is_kept_when_base_is_hidden() -> None:
    """Current middleware shape: __user copy is the only visible user message."""
    channel_values = {
        "messages": [_hidden_reminder(), _user_copy(), _ai_reply()],
    }
    patch_channel_values_messages(channel_values)
    assert _ids(channel_values["messages"]) == [BASE_ID, USER_COPY_ID, "ai-1"]
    assert channel_values["messages"][1]["content"] == "搜索点新闻"


def test_user_copy_kept_even_without_any_base_id_message() -> None:
    """No message with the base id at all → nothing to duplicate → keep the copy."""
    orphan = _user_copy("今日国内外综合头条新闻")
    channel_values = {"messages": [orphan, _ai_reply("开始搜索")]}
    patch_channel_values_messages(channel_values)
    assert _ids(channel_values["messages"]) == [USER_COPY_ID, "ai-1"]


def test_legacy_duplicate_at_end_is_stripped() -> None:
    """Legacy shape: visible original stays in place, duplicate __user copy at end."""
    visible_original = {
        "id": BASE_ID,
        "type": "human",
        "content": "搜索点新闻",
        "additional_kwargs": {"timestamp": "2026-08-01T10:00:00"},
    }
    duplicate = _user_copy()
    channel_values = {
        "messages": [visible_original, _ai_reply(), duplicate],
    }
    patch_channel_values_messages(channel_values)
    assert _ids(channel_values["messages"]) == [BASE_ID, "ai-1"]


def test_legacy_duplicate_visible_original_hidden_base_keeps_copy() -> None:
    """The companion message must be VISIBLE for the copy to count as a duplicate.

    If the base-id message is hidden (``hide_from_ui``) the __user copy is
    still the only visible user message and must survive.
    """
    channel_values = {
        "messages": [_hidden_reminder(), _user_copy()],
    }
    patch_channel_values_messages(channel_values)
    assert _ids(channel_values["messages"]) == [BASE_ID, USER_COPY_ID]


def test_mixed_turns_each_handled_independently() -> None:
    """A legacy turn (duplicate stripped) and a current turn (copy kept) coexist."""
    legacy_id = "11111111-2222-3333-4444-555555555555"
    messages = [
        {  # legacy turn: visible original …
            "id": legacy_id,
            "type": "human",
            "content": "旧消息",
            "additional_kwargs": {"timestamp": "2026-08-01T09:00:00"},
        },
        _ai_reply("旧回复"),
        {  # … plus its end-appended duplicate (legacy shape)
            "id": legacy_id + "__user",
            "type": "human",
            "content": "旧消息",
            "name": "user-input",
            "additional_kwargs": {},
        },
        # current turn: hidden reminder + visible __user copy
        _hidden_reminder("新消息"),
        _user_copy("新消息"),
        {
            "id": "ai-2",
            "type": "ai",
            "content": "新回复",
            "additional_kwargs": {"timestamp": "2026-08-01T10:00:10"},
        },
    ]
    channel_values = {"messages": messages}
    patch_channel_values_messages(channel_values)
    assert _ids(channel_values["messages"]) == [
        legacy_id,
        "ai-1",
        BASE_ID,
        USER_COPY_ID,
        "ai-2",
    ]


def test_input_not_mutated_beyond_documented_patching() -> None:
    """Non-__user messages keep their identity/order; AI content patching aside,
    the function must not drop or reorder unrelated messages."""
    original = [
        _hidden_reminder(),
        _user_copy(),
        _ai_reply(),
        {"id": "tool-1", "type": "tool", "content": "ok", "additional_kwargs": {}},
    ]
    snapshot = copy.deepcopy(original)
    channel_values = {"messages": copy.deepcopy(original)}
    patch_channel_values_messages(channel_values)
    kept = channel_values["messages"]
    assert len(kept) == 4
    for want, got in zip(snapshot, kept):
        assert want["id"] == got["id"]
        assert want["type"] == got["type"]
        assert want["content"] == got["content"]
