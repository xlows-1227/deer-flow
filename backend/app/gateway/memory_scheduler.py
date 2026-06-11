"""Background scheduler for v2 daily memory rollups."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import FastAPI

from deerflow.agents.memory.consolidation import ProfileConsolidator
from deerflow.agents.memory.rollup import DailyRollupService
from deerflow.agents.memory.storage_v2 import get_memory_storage_v2
from deerflow.config.app_config import get_app_config

logger = logging.getLogger(__name__)

_MEMORY_SCHEDULER_INTERVAL_SECONDS = 60.0


async def memory_rollup_loop(app: FastAPI, *, interval_seconds: float = _MEMORY_SCHEDULER_INTERVAL_SECONDS) -> None:
    """Run daily memory rollups on a lightweight background loop."""
    last_run_keys: set[str] = set()
    while True:
        try:
            config = get_app_config().memory
            if config.enabled and config.v2_enabled and config.daily_rollup_enabled:
                now = datetime.now(UTC)
                current_hhmm = now.strftime("%H:%M")
                if current_hhmm >= config.daily_rollup_time:
                    run_key = now.date().isoformat()
                    if run_key not in last_run_keys:
                        await asyncio.to_thread(_run_due_rollups_once)
                        last_run_keys.add(run_key)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Daily memory rollup loop iteration failed")
        await asyncio.sleep(interval_seconds)


def _run_due_rollups_once() -> None:
    storage = get_memory_storage_v2()
    rollup = DailyRollupService(storage=storage)
    consolidator = ProfileConsolidator(storage=storage)
    for user_id, date in storage.list_rollup_targets():
        summary = rollup.rollup_date(user_id, date, source_kind="scheduled")
        if summary is not None:
            consolidator.rebuild_profile(user_id)


def start_memory_rollup_loop(app: FastAPI) -> None:
    """Start the memory rollup loop if needed."""
    task = getattr(app.state, "memory_rollup_loop_task", None)
    if task is not None and not task.done():
        return
    app.state.memory_rollup_loop_task = asyncio.create_task(memory_rollup_loop(app), name="deerflow-memory-rollup-loop")
    logger.info("Daily memory rollup loop started")


async def stop_memory_rollup_loop(app: FastAPI) -> None:
    """Stop the memory rollup loop."""
    task = getattr(app.state, "memory_rollup_loop_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    app.state.memory_rollup_loop_task = None
    logger.info("Daily memory rollup loop stopped")
