"""Parser for Mini-Agent run logs.

Mini-Agent writes one human-readable ``agent_run_YYYYMMDD_HHMMSS.log`` file per
session under ``~/.mini-agent/log/``. Each file is a sequence of delimited
sections like::

    [1] REQUEST
    Timestamp: 2026-06-13 10:30:00.123
    ----------------------------------------------------------------------------
    LLM Request:

    {"messages": [...], "tools": [...]}

    [2] RESPONSE
    ...
    LLM Response:

    {"content": "...", "thinking": "...", "tool_calls": [...], "finish_reason": "..."}

    [3] TOOL_RESULT
    ...
    Tool Execution:

    {"tool_name": "read_file", "arguments": {...}, "success": true, "result": "..."}

The REQUEST block contains a *snapshot* of the entire ``self.messages`` list at
that step, so it grows monotonically. We therefore only need the **final**
REQUEST block in a file to recover the full conversation, plus the metadata
(timestamp, run id) from the header.

This module turns one ``.log`` file into a :class:`ParsedSession` holding an
ordered list of Mini-Agent messages (same shape as ``mini_agent.schema.Message``)
ready to be fed into :mod:`converter`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# A section header looks like:
#   [12] REQUEST
#   [13] RESPONSE
#   [14] TOOL_RESULT
# followed by a Timestamp line and a line of dashes.
_HEADER_RE = re.compile(r"^\[(\d+)\]\s+(REQUEST|RESPONSE|TOOL_RESULT)\s*$")
_TS_RE = re.compile(r"^Timestamp:\s*(.+?)\s*$")
# The JSON payload for each section lives after a leading label line such as
# "LLM Request:" / "LLM Response:" / "Tool Execution:" and a blank line. We
# grab the first '{' and let json.loads find its matching '}'.
_JSON_START_RE = re.compile(r"^(LLM Request|LLM Response|Tool Execution)\s*:\s*$")


@dataclass
class MiniMessage:
    """One Mini-Agent message (mirrors mini_agent.schema.Message)."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | list
    thinking: str | None = None
    tool_calls: list | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ParsedSession:
    """One recovered Mini-Agent conversation."""

    source_file: Path
    started_at: datetime | None
    messages: list[MiniMessage] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        # Ignore the leading system message when deciding emptiness.
        return len([m for m in self.messages if m.role != "system"]) == 0

    def first_user_text(self) -> str:
        for m in self.messages:
            if m.role == "user" and isinstance(m.content, str) and m.content.strip():
                return m.content.strip()
        return ""


def _extract_json(block: str) -> dict | None:
    """Pull the first JSON object out of a section body.

    The body always starts with a label line ("LLM Request:", ...) then a
    blank line, then the pretty-printed JSON. We locate the first ``{`` and
    decode greedily; if that fails we fall back to a balanced-brace scan.
    """
    start = block.find("{")
    if start == -1:
        return None
    candidate = block[start:]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # Balanced brace scan: decode the largest prefix that is valid JSON.
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(candidate):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(candidate[: i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _iter_sections(text: str):
    """Yield ``(index, kind, timestamp, body)`` tuples for every section."""
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        m = _HEADER_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        index = int(m.group(1))
        kind = m.group(2)
        # Collect the body until the next header (or EOF).
        body_start = i + 1
        j = body_start
        while j < n and not _HEADER_RE.match(lines[j].strip()):
            j += 1
        body_lines = lines[body_start:j]
        # First non-empty body line is usually the Timestamp.
        ts: datetime | None = None
        body_offset = 0
        for k, ln in enumerate(body_lines):
            stripped = ln.strip()
            if not stripped:
                continue
            tm = _TS_RE.match(stripped)
            if tm:
                try:
                    ts = datetime.fromisoformat(tm.group(1))
                except ValueError:
                    raw = tm.group(1)
                    # Microsecond-truncated form like "2026-06-13 10:30:00.123"
                    try:
                        ts = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S.%f")
                    except ValueError:
                        try:
                            ts = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            ts = None
                body_offset = k + 1
                break
            # If the first non-empty line is not a Timestamp, stop looking.
            break
        body = "\n".join(body_lines[body_offset:])
        yield index, kind, ts, body
        i = j


def _parse_header_datetime(text: str) -> datetime | None:
    """Pull the run start time from the file header line.

    The header looks like::

        ================================================================================
        Agent Run Log - 2026-06-13 10:30:00
        ================================================================================
    """
    m = re.search(r"Agent Run Log\s*-\s*(.+?)\s*$", text, re.MULTILINE)
    if not m:
        return None
    raw = m.group(1)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def parse_log_file(path: Path) -> ParsedSession:
    """Parse a single Mini-Agent ``.log`` file into a :class:`ParsedSession`.

    Strategy: REQUEST blocks hold the growing message-history snapshot, so the
    *last* REQUEST in the file is the authoritative full transcript. We fall
    back to replaying RESPONSE/TOOL_RESULT blocks if no REQUEST carries
    messages (defensive; shouldn't normally happen).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    session = ParsedSession(
        source_file=path,
        started_at=_parse_header_datetime(text),
    )

    last_request_messages: list[MiniMessage] | None = None
    # Track the chronological event stream as a fallback / sanity source.
    events: list[tuple[int, str, datetime | None, dict]] = []
    for index, kind, ts, body in _iter_sections(text):
        payload = _extract_json(body)
        if payload is None:
            continue
        events.append((index, kind, ts, payload))
        if kind == "REQUEST":
            msgs = payload.get("messages")
            if isinstance(msgs, list) and msgs:
                last_request_messages = [_coerce_message(m) for m in msgs]

    if last_request_messages is not None:
        session.messages = last_request_messages
    else:
        # Fallback: reconstruct from the event stream.
        session.messages = _reconstruct_from_events(events)

    return session


def _coerce_message(raw: dict) -> MiniMessage:
    """Best-effort conversion of a raw message dict into a MiniMessage."""
    return MiniMessage(
        role=raw.get("role", "user"),
        content=raw.get("content", ""),
        thinking=raw.get("thinking"),
        tool_calls=raw.get("tool_calls"),
        tool_call_id=raw.get("tool_call_id"),
        name=raw.get("name"),
    )


def _reconstruct_from_events(
    events: list[tuple[int, str, datetime | None, dict]],
) -> list[MiniMessage]:
    """Rebuild a message list when no full REQUEST snapshot is available.

    Walks RESPONSE + TOOL_RESULT events in order and synthesises the assistant
    and tool messages they imply. System/user messages cannot be recovered
    this way, so this is strictly a degraded fallback.
    """
    messages: list[MiniMessage] = []
    for _index, kind, _ts, payload in events:
        if kind == "RESPONSE":
            messages.append(
                MiniMessage(
                    role="assistant",
                    content=payload.get("content", "") or "",
                    thinking=payload.get("thinking"),
                    tool_calls=payload.get("tool_calls"),
                )
            )
        elif kind == "TOOL_RESULT":
            content = payload.get("result") if payload.get("success") else (
                f"Error: {payload.get('error')}"
            )
            messages.append(
                MiniMessage(
                    role="tool",
                    content=content or "",
                    name=payload.get("tool_name"),
                )
            )
    return messages
