"""Background loop that resolves ``unscanned`` skill security statuses.

This is deliberately *not* the user-facing task scheduler: that one exists for
user-scheduled agent runs (its jobs are persisted, user-visible, and billed to
the user).  Here we need a cheap internal periodic job, so we mirror
``memory_scheduler``: one asyncio task, tick every 30s, and run a batch only
when ``security_rescan_interval_seconds`` has elapsed.

Rate limiting has three layers:
* interval between batches (config ``security_rescan_interval_seconds``),
* batch size (``security_rescan_batch_size``), so a backlog of legacy skills is
  drained gradually,
* per-batch concurrency (``security_rescan_concurrency``), plus the per-skill
  in-flight lock in ``deerflow.skills.security_rescan``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from fastapi import FastAPI

from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.skills.security_rescan import rescan_unscanned_skills
from deerflow.skills.storage import get_or_new_skill_storage

logger = logging.getLogger(__name__)

_TICK_SECONDS = 30.0


def _skills_root(config: AppConfig) -> Path:
    return get_or_new_skill_storage(app_config=config).get_skills_root_path()


async def skill_security_rescan_loop(app: FastAPI, *, tick_seconds: float = _TICK_SECONDS) -> None:
    next_run = 0.0
    while True:
        try:
            config = get_app_config()
            skill_config = config.skill_evolution
            if getattr(skill_config, "security_rescan_enabled", True):
                now = time.monotonic()
                if now >= next_run:
                    summary = await rescan_unscanned_skills(
                        _skills_root(config),
                        batch_size=getattr(skill_config, "security_rescan_batch_size", 5),
                        concurrency=getattr(skill_config, "security_rescan_concurrency", 1),
                        model_name=skill_config.moderation_model_name,
                    )
                    if summary["processed"]:
                        logger.info(
                            "Skill security rescan batch: candidates=%d processed=%d passed=%d failed=%d unscanned=%d changed=%d",
                            summary["candidates"],
                            summary["processed"],
                            summary["passed"],
                            summary["failed"],
                            summary["unscanned"],
                            summary["changed"],
                        )
                    next_run = time.monotonic() + max(
                        1, getattr(skill_config, "security_rescan_interval_seconds", 300)
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Skill security rescan loop iteration failed")
        await asyncio.sleep(tick_seconds)


def start_skill_security_rescan_loop(app: FastAPI) -> None:
    task = getattr(app.state, "skill_security_rescan_loop_task", None)
    if task is not None and not task.done():
        return
    app.state.skill_security_rescan_loop_task = asyncio.create_task(
        skill_security_rescan_loop(app),
        name="deerflow-skill-security-rescan-loop",
    )
    logger.info("Skill security rescan loop started")


async def stop_skill_security_rescan_loop(app: FastAPI) -> None:
    task = getattr(app.state, "skill_security_rescan_loop_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    app.state.skill_security_rescan_loop_task = None
    logger.info("Skill security rescan loop stopped")
