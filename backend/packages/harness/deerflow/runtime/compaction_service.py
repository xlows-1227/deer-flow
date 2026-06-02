"""Lightweight compaction service.

Performs Pi-agent-style compaction directly on a thread's checkpoint
without launching a full agent run.  This is used by the
``POST /api/threads/{thread_id}/compact`` endpoint.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import RemoveMessage
from langgraph.runtime import Runtime

from deerflow.agents.lead_agent.agent import _create_summarization_middleware
from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.runtime.serialization import serialize_channel_values
from deerflow.utils.time import now_iso

logger = logging.getLogger(__name__)


class _BufferedCompactionJournal:
    """Minimal RunJournal-compatible sink used by manual compaction."""

    def __init__(self, *, thread_id: str, run_id: str, event_store: Any) -> None:
        self._thread_id = thread_id
        self._run_id = run_id
        self._event_store = event_store
        self._events: list[dict[str, Any]] = []

    def record_middleware(self, tag: str, *, name: str, hook: str, action: str, changes: dict) -> None:
        self._events.append(
            {
                "thread_id": self._thread_id,
                "run_id": self._run_id,
                "event_type": f"middleware:{tag}",
                "category": "middleware",
                "content": {"name": name, "hook": hook, "action": action, "changes": changes},
                "metadata": {},
                "created_at": now_iso(),
            }
        )

    async def flush(self) -> None:
        if not self._events:
            return
        events = self._events
        self._events = []
        await self._event_store.put_batch(events)


async def compact_thread_checkpoint(
    *,
    checkpointer: Any,
    thread_id: str,
    custom_instructions: str | None = None,
    app_config: AppConfig | None = None,
    event_store: Any | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Manually compact the conversation history for a thread.

    Steps:
    1. Read the latest checkpoint for the thread.
    2. Build a SummarizationMiddleware instance from config.
    3. Run the middleware's compaction logic against the current messages.
    4. Write the compacted checkpoint back.
    5. Return the new serialized state.
    """
    app_config = app_config or get_app_config()
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    # 1. Read latest checkpoint
    checkpoint_tuple = await checkpointer.aget_tuple(config)
    if checkpoint_tuple is None:
        raise ValueError(f"Thread {thread_id} not found")

    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    metadata = getattr(checkpoint_tuple, "metadata", {}) or {}
    channel_values: dict[str, Any] = dict(checkpoint.get("channel_values", {}))
    messages = list(channel_values.get("messages", []))

    if not messages:
        return serialize_channel_values(channel_values)

    # 2. Build middleware (reuse factory so settings stay in sync)
    middleware = _create_summarization_middleware(app_config=app_config)
    if middleware is None:
        raise RuntimeError("Summarization is disabled in config")

    # 3. Prepare a fake state and runtime for the middleware
    fake_state: dict[str, Any] = {"messages": messages}
    runtime_ctx: dict[str, Any] = {"thread_id": thread_id, "force_compact": True}
    if custom_instructions:
        runtime_ctx["compact_instructions"] = custom_instructions
    journal = None
    if event_store is not None and run_id:
        journal = _BufferedCompactionJournal(thread_id=thread_id, run_id=run_id, event_store=event_store)
        runtime_ctx["__run_journal"] = journal
    runtime = Runtime(context=runtime_ctx, store=None)

    # 4. Run compaction (async path)
    update = await middleware.abefore_model(fake_state, runtime)
    if update is None:
        # Nothing to compact (e.g. below threshold and force_compact not honoured)
        logger.info("No compaction needed for thread %s", thread_id)
        return serialize_channel_values(channel_values)

    # 5. Apply update to channel values.
    # The middleware returns [RemoveMessage(REMOVE_ALL_MESSAGES), summary, ...preserved].
    # add_messages reducer interprets RemoveMessage(REMOVE_ALL_MESSAGES) as "delete all".
    # For direct checkpoint manipulation we reconstruct the final list.
    final_messages: list[Any] = []
    for msg in update.get("messages", []):
        if isinstance(msg, RemoveMessage):
            continue
        final_messages.append(msg)

    channel_values["messages"] = final_messages

    # 6. Write back
    checkpoint["channel_values"] = channel_values
    metadata = {**metadata, "updated_at": now_iso(), "source": "compaction"}

    await checkpointer.aput(config, checkpoint, metadata, {})
    if journal is not None:
        try:
            await journal.flush()
        except Exception:
            logger.warning("Failed to record manual compaction event for thread %s", thread_id, exc_info=True)

    logger.info("Compacted thread %s: %d -> %d messages", thread_id, len(messages), len(final_messages))
    return serialize_channel_values(channel_values)
