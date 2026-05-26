"""Scheduled task persistence helpers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.scheduled_task.model import ScheduledTaskRow
from deerflow.persistence.scheduled_task.store import MemoryScheduledTaskStore, ScheduledTaskRepository


def make_scheduled_task_store(session_factory: async_sessionmaker[AsyncSession] | None):
    if session_factory is None:
        return MemoryScheduledTaskStore()
    return ScheduledTaskRepository(session_factory)


__all__ = [
    "MemoryScheduledTaskStore",
    "ScheduledTaskRepository",
    "ScheduledTaskRow",
    "make_scheduled_task_store",
]
