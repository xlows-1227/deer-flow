"""Reusable thread creation service shared by internal and External APIs."""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.checkpoint.base import empty_checkpoint

from deerflow.utils.time import now_iso


async def create_empty_thread(
    *,
    thread_store,
    checkpointer,
    assistant_id: str | None,
    metadata: dict[str, Any],
    thread_id: str | None = None,
) -> dict[str, Any]:
    resolved_thread_id = thread_id or str(uuid.uuid4())
    existing = await thread_store.get(resolved_thread_id)
    if existing is not None:
        return existing

    record = await thread_store.create(
        resolved_thread_id,
        assistant_id=assistant_id,
        metadata=metadata,
    )
    now = now_iso()
    config = {"configurable": {"thread_id": resolved_thread_id, "checkpoint_ns": ""}}
    checkpoint_metadata = {
        "step": -1,
        "source": "input",
        "writes": None,
        "parents": {},
        **metadata,
        "created_at": now,
    }
    try:
        await checkpointer.aput(config, empty_checkpoint(), checkpoint_metadata, {})
    except Exception:
        await thread_store.delete(resolved_thread_id)
        raise
    return record
