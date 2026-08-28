"""Explicit public serializers for published-Agent API responses."""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Any

from deerflow.agents.middlewares.token_usage_middleware import PUBLISHED_RUN_TOKEN_BUDGET_ERROR

_FORBIDDEN_KEY_PARTS = (
    "owner_user",
    "release_id",
    "model_name",
    "instructions",
    "secret",
    "hash",
    "connector",
    "skill_revision",
    "credential_id",
    "thread_id",
    "configurable",
)

_SYSTEM_REMINDER_RE = re.compile(r"<system-rem>.*?</system-rem>", re.DOTALL)
_TOOL_CALL_SECTION_RE = re.compile(r"<\|tool_calls_section_begin\|>.*?<\|tool_calls_section_end\|>", re.DOTALL)
_SINGLE_MARKER_RE = re.compile(r"<\|[^>]+\|>")
_INTERNAL_MARKERS = ["[工具调用已省略]", "[内部消息]"]
# Match: [工具调用: tool_name]  or  [工具调用已省略]  — 完整中文字符 + 括号内容
_TOOL_OMISSION_NAMED_RE = re.compile(r"\[工具调用:[^\]]*\]")
# Also strip inline Chinese tool-call markers like "[工具调用: query_database]..." that appear multiple times
_TOOL_INLINE_CHINESE_RE = re.compile(r"【工具调用[：:][^】]*】")

# --- Pretty-print helpers for public answer text ---------------------------------
# Heading markers: ## / ###  followed by digits or Chinese, e.g. "###1." "### 标题"
# Anchor at a non-whitespace or punctuation char so we don't match partial
# markdown link syntax or headings that are already on their own line.
_HEADING_LINE_RE = re.compile(r"(?<![\n#])(#{2,3}\s*\d*[\.\s、、]*[^\n#]{1,80})")
# Markdown table *block*: a contiguous pipe-heavy substring with at least one
# `---` separator (meaning it is a real markdown table, not a random pipe
# chain inside sentence text).  Greedy + dotall on a single source line.
_TABLE_BLOCK_RE = re.compile(
    r"(\|(?:[^|\n]*\|)+(?:\|-{2,}){2,}\|(?:[^|\n]*\|)+)",
)
# Table separator row fragment, used later inside a block.
_TABLE_SEP_FRAGMENT_RE = re.compile(r"(\|(?:\s*:?-+:?\s*\|){2,})")
# SQL fence markers for markdown code blocks
_SQL_FENCE_RE = re.compile(r"```sql", re.IGNORECASE)


def _format_public_answer(text: str) -> str:
    """Insert line breaks around markdown structural markers so the raw JSON
    string is readable even without a markdown renderer (e.g. Postman Raw view).

    Safe for downstream markdown consumers: we only *add* newlines around
    structural markers that are already intended to break lines; we never
    remove content or join lines.
    """
    if not text:
        return text
    s = text

    # 1. Ensure heading markers (##title, ###1.标题) sit on their own lines.
    #    We match text captured by _HEADING_LINE_RE and prepend newline unless
    #    already preceded by a newline (negative lookbehind in the regex).
    def _pad_heading(match: re.Match[str]) -> str:
        chunk = match.group(1)
        if chunk.startswith("\n"):
            return chunk
        return "\n" + chunk

    s = _HEADING_LINE_RE.sub(_pad_heading, s)

    # 2. Process markdown table blocks: a single "flattened" substring like
    #    `|A|B||---|---||1|2||3|4|` becomes 4 separate lines:
    #        |A|B|
    #        |---|---|
    #        |1|2|
    #        |3|4|
    def _format_table_block(match: re.Match[str]) -> str:
        block = match.group(1).strip()
        # Split pipe-separated tokens; cells are everything between '|'s.
        raw_cells = block.split("|")
        # Remove leading & trailing empties (they come from block opening/closing |)
        cells: list[str] = []
        if raw_cells and raw_cells[0] == "":
            raw_cells = raw_cells[1:]
        if raw_cells and raw_cells[-1] == "":
            raw_cells = raw_cells[:-1]
        cells = raw_cells

        # Infer column count from the first separator fragment (|---:---|---|).
        # The separator row has (column_count + 1) pipe characters in its
        # standalone form but here appears inline as a consecutive sequence of
        # `---` fragments separated by pipes.
        sep_indices = [
            i for i, c in enumerate(cells) if re.fullmatch(r"\s*:?-+:?\s*", c)
        ]
        # Columns = number of consecutive separator cells we find
        if sep_indices:
            # Group consecutive indices
            groups: list[list[int]] = []
            current_group: list[int] = [sep_indices[0]]
            for idx in sep_indices[1:]:
                if idx == current_group[-1] + 1:
                    current_group.append(idx)
                else:
                    groups.append(current_group)
                    current_group = [idx]
            groups.append(current_group)
            col_count = max((len(g) for g in groups), default=0)
        else:
            col_count = 0

        rows: list[list[str]] = []
        current_row: list[str] = []

        def flush_row() -> None:
            if current_row:
                rows.append(list(current_row))
                current_row.clear()

        idx = 0
        while idx < len(cells):
            cell = cells[idx]
            if cell == "" and current_row:
                # Empty cell = row boundary (two consecutive rows share the
                # same pipe, creating the `||` join).
                flush_row()
                idx += 1
                continue
            current_row.append(cell)
            # If we have a known column count and the row is full, emit it.
            if col_count > 0 and len(current_row) == col_count:
                flush_row()
                # Advance past the following empty cell if present (which is
                # the explicit boundary).  Skip it so we don't emit an empty
                # row on the next iteration.
                if idx + 1 < len(cells) and cells[idx + 1] == "":
                    idx += 2
                    continue
            idx += 1
        flush_row()

        if not rows:
            return "\n" + block + "\n"

        lines = ["|" + "|".join(r) + "|" for r in rows]
        return "\n" + "\n".join(lines) + "\n"

    s = _TABLE_BLOCK_RE.sub(_format_table_block, s)

    # 3. Ensure SQL fences (```sql ... ```) are preceded & followed by newlines.
    def _pad_sql_start(match: re.Match[str]) -> str:
        token = match.group(0)
        return ("\n" if not s[: match.start()].endswith("\n") else "") + token + "\n"

    s = _SQL_FENCE_RE.sub(_pad_sql_start, s)
    # Prefix close-fence with newline unless already has one; postfix too.
    # We only touch ``` that closes a fenced block: look for closing markers.
    s = re.sub(
        r"(?<!\n)```(?=\s*[^`]|$)",
        lambda m: "\n" + m.group(0),
        s,
    )
    s = re.sub(
        r"```(?!\s*\n)",
        lambda m: m.group(0) + "\n",
        s,
    )

    # 5. Collapse runs of 3+ newlines to exactly 2 newlines.
    s = re.sub(r"\n{3,}", "\n\n", s)

    # 6. Remove leading whitespace-only blank lines, keep trailing single newline.
    return s.strip()


def _clean_content_for_public(content: str) -> str:
    """Remove internal markers from content before exposing to public API."""
    if not content:
        return content
    cleaned = _SYSTEM_REMINDER_RE.sub("", content)
    cleaned = _TOOL_CALL_SECTION_RE.sub("", cleaned)
    cleaned = _SINGLE_MARKER_RE.sub("", cleaned)
    # Remove [工具调用: tool_name] markers
    cleaned = _TOOL_OMISSION_NAMED_RE.sub("", cleaned)
    # Remove 【工具调用:xxx】 markers (both full-width and half-width colon)
    cleaned = _TOOL_INLINE_CHINESE_RE.sub("", cleaned)
    for marker in _INTERNAL_MARKERS:
        cleaned = cleaned.replace(marker, "")
    # Restore spaces between English words if the model produced concatenated
    # output (e.g. "Itseemsthemessage").  Must run BEFORE markdown pretty-print
    # so separator/header detection works on properly spaced text.
    from deerflow.runtime.runs.worker import _restore_english_spaces

    cleaned = _restore_english_spaces(cleaned)
    cleaned = _format_public_answer(cleaned)
    return cleaned


def assert_public_payload_safe(value: Any, *, path: str = "$") -> None:
    """Recursively fail if an internal authority field reaches a response."""
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key).lower()
            if any(part in key for part in _FORBIDDEN_KEY_PARTS):
                raise ValueError(f"unsafe public response field at {path}.{raw_key}")
            assert_public_payload_safe(item, path=f"{path}.{raw_key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_public_payload_safe(item, path=f"{path}[{index}]")


def _finish(payload: dict[str, Any]) -> dict[str, Any]:
    assert_public_payload_safe(payload)
    return payload


def serialize_agent_metadata(agent: dict[str, Any]) -> dict[str, Any]:
    """Serialize the public metadata allowlist for a published Agent."""
    return _finish(
        {
            "agent_id": str(agent["id"]),
            "display_name": str(agent["display_name"]),
            "description": agent.get("description"),
            "avatar": agent.get("avatar_ref"),
        }
    )


def serialize_agent_capabilities(
    agent_id: str,
    *,
    skills: tuple[tuple[str, str, str], ...],
    model_name: str,
    model_display_name: str | None,
    supports_thinking: bool,
    supports_reasoning_effort: bool,
    supports_vision: bool,
    model_available: bool,
) -> dict[str, Any]:
    """Serialize the safe, currently effective integration capabilities."""
    models = []
    if model_available:
        models.append(
            {
                "name": model_name,
                "display_name": model_display_name,
                "supports_thinking": supports_thinking,
                "supports_reasoning_effort": supports_reasoning_effort,
                "supports_vision": supports_vision,
            }
        )
    return _finish(
        {
            "agent_id": agent_id,
            "skills": [
                {
                    "name": name,
                    "display_name": display_name,
                    "description": description,
                }
                for name, display_name, description in sorted(set(skills))
            ],
            "models": models,
        }
    )


def serialize_agent_conversation(row: dict[str, Any]) -> dict[str, Any]:
    """Serialize the public state of a credential-scoped conversation."""
    return _finish(
        {
            "conversation_id": str(row["conversation_id"]),
            "status": str(row["status"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


def _status(value: Any) -> str:
    raw = value.value if isinstance(value, Enum) else str(value or "error")
    return {
        "success": "completed",
        "error": "failed",
        "interrupted": "cancelled",
    }.get(raw, raw)


# Safe, non-sensitive failure messages that may be returned on the public API.
_PUBLIC_RUN_ERROR_ALLOWLIST = frozenset(
    {
        "The run failed.",
        PUBLISHED_RUN_TOKEN_BUDGET_ERROR,
    }
)


def serialize_agent_run(row: Any, *, conversation_id: str) -> dict[str, Any]:
    """Serialize one run without leaking internal runtime authority."""
    getter = (lambda key, default=None: getattr(row, key, default)) if not isinstance(row, dict) else row.get
    status = _status(getter("status"))
    raw_error = getter("error")
    if status == "failed":
        error = raw_error if isinstance(raw_error, str) and raw_error in _PUBLIC_RUN_ERROR_ALLOWLIST else "The run failed."
    else:
        error = None
    answer = getter("last_ai_message") if status == "completed" else None
    if isinstance(answer, str):
        answer = _clean_content_for_public(answer)
    elif answer is None:
        pass
    payload = {
        "run_id": str(getter("run_id")),
        "conversation_id": conversation_id,
        "status": status,
        "answer": answer,
        "error": error,
        "created_at": getter("created_at"),
        "updated_at": getter("updated_at"),
    }
    return _finish(payload)


def sanitize_stream_payload(value: Any) -> Any:
    """Keep only public message content/status fields in SSE payloads."""
    if value is None or isinstance(value, (str, int, float, bool, datetime, date)):
        return value.isoformat() if isinstance(value, (datetime, date)) else value
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            cleaned = sanitize_stream_payload(item)
            # If item had a content field that was cleaned to empty string, filter it
            if isinstance(cleaned, dict) and "content" in cleaned and cleaned["content"] == "":
                # Skip items where content was fully cleaned (e.g., tool call markers only)
                continue
            result.append(cleaned)
        return result
    if not isinstance(value, dict):
        return str(value)
    allowed = {
        "id",
        "run_id",
        "conversation_id",
        "type",
        "role",
        "content",
        "status",
        "error",
        "messages",
        "delta",
    }
    sanitized = {}
    for key, item in value.items():
        if str(key) not in allowed:
            continue
        cleaned_item = sanitize_stream_payload(item)
        if str(key) == "content" and isinstance(cleaned_item, str):
            cleaned_item = _clean_content_for_public(cleaned_item)
        elif str(key) == "content" and isinstance(cleaned_item, list):
            # Handle list-type content (e.g., list of text blocks)
            cleaned_list = []
            for block in cleaned_item:
                if isinstance(block, str):
                    cleaned_block = _clean_content_for_public(block)
                    if cleaned_block:
                        cleaned_list.append(cleaned_block)
                elif isinstance(block, dict) and "text" in block:
                    cleaned_text = _clean_content_for_public(str(block["text"]))
                    if cleaned_text:
                        cleaned_list.append({**block, "text": cleaned_text})
                else:
                    cleaned_list.append(block)
            cleaned_item = cleaned_list
        sanitized[str(key)] = cleaned_item
    assert_public_payload_safe(sanitized)
    return sanitized


__all__ = [
    "assert_public_payload_safe",
    "sanitize_stream_payload",
    "serialize_agent_capabilities",
    "serialize_agent_conversation",
    "serialize_agent_metadata",
    "serialize_agent_run",
]
