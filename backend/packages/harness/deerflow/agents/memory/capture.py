"""Capture sanitized conversation snippets for daily memory rollups."""

from __future__ import annotations

import re
import uuid
from typing import Any

from deerflow.agents.memory.models import MemoryRollupInput
from deerflow.agents.memory.safety import scrub_memory_text
from deerflow.agents.memory.storage import utc_now_iso_z
from deerflow.agents.memory.storage_v2 import MemoryStorageV2, get_memory_storage_v2, local_date_from_utc

_INJECTED_CONTEXT_RE = re.compile(
    r"<system-reminder>[\s\S]*?</system-reminder>\s*|<memory>[\s\S]*?</memory>\s*",
    re.IGNORECASE,
)


def capture_rollup_input(
    *,
    user_id: str,
    thread_id: str,
    messages: list[Any],
    date: str | None = None,
    run_id: str | None = None,
    storage: MemoryStorageV2 | None = None,
) -> MemoryRollupInput | None:
    """Persist sanitized messages as rollup input."""
    storage = storage or get_memory_storage_v2()
    date = date or local_date_from_utc()
    formatted = scrub_memory_text(_format_user_evidence(messages))
    if not formatted:
        return None
    rollup_input = MemoryRollupInput(
        id=f"rollup_{uuid.uuid4().hex[:12]}",
        userId=user_id,
        date=date,
        threadId=thread_id,
        runId=run_id,
        messages=[{"role": "conversation", "content": formatted[:4000]}],
        createdAt=utc_now_iso_z(),
    )
    return storage.save_rollup_input(user_id, rollup_input)


def _format_user_evidence(messages: list[Any]) -> str:
    """Format only genuine user-authored text as rollup evidence."""
    lines: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            role = message.get("type") or message.get("role")
            content = message.get("content", "")
        else:
            role = getattr(message, "type", None) or getattr(message, "role", None)
            content = getattr(message, "content", "")
        if role not in {"human", "user"}:
            continue

        if isinstance(content, list):
            content = " ".join(
                part if isinstance(part, str) else str(part.get("text", ""))
                for part in content
                if isinstance(part, str) or isinstance(part, dict)
            )
        cleaned = _INJECTED_CONTEXT_RE.sub("", str(content)).strip()
        if cleaned:
            lines.append(f"User: {cleaned[:1000]}")
    return "\n\n".join(lines)
