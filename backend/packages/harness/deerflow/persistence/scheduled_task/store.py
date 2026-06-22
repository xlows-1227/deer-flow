"""Scheduled task persistence implementations."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.scheduled_task.model import ScheduledTaskRow
from deerflow.utils.time import coerce_iso

TaskDict = dict[str, Any]


def _copy_task(task: Mapping[str, Any]) -> TaskDict:
    return dict(task)


def _normalize_datetime(value: Any) -> Any:
    if isinstance(value, datetime):
        return coerce_iso(value)
    return value


def _row_to_dict(row: ScheduledTaskRow) -> TaskDict:
    data = row.to_dict()
    for key in (
        "last_run_at",
        "next_run_at",
        "created_at",
        "updated_at",
    ):
        data[key] = _normalize_datetime(data.get(key))
    return data


class ScheduledTaskRepository:
    """SQLAlchemy-backed scheduled task store."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, values: Mapping[str, Any]) -> TaskDict:
        row = ScheduledTaskRow(**dict(values))
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_dict(row)

    async def get(self, task_id: str, *, user_id: str | None) -> TaskDict | None:
        async with self._sf() as session:
            row = await session.get(ScheduledTaskRow, task_id)
            if row is None:
                return None
            if user_id is not None and row.user_id != user_id:
                return None
            return _row_to_dict(row)

    async def list(self, *, user_id: str | None) -> list[TaskDict]:
        stmt = select(ScheduledTaskRow).order_by(ScheduledTaskRow.created_at.desc())
        if user_id is not None:
            stmt = stmt.where(ScheduledTaskRow.user_id == user_id)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_dict(row) for row in rows]

    async def update(self, task_id: str, values: Mapping[str, Any], *, user_id: str | None) -> TaskDict | None:
        async with self._sf() as session:
            row = await session.get(ScheduledTaskRow, task_id)
            if row is None:
                return None
            if user_id is not None and row.user_id != user_id:
                return None
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return _row_to_dict(row)

    async def delete(self, task_id: str, *, user_id: str | None) -> bool:
        stmt = delete(ScheduledTaskRow).where(ScheduledTaskRow.id == task_id)
        if user_id is not None:
            stmt = stmt.where(ScheduledTaskRow.user_id == user_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount != 0

    async def list_due(self, *, now: datetime, limit: int = 20) -> list[TaskDict]:
        stmt = (
            select(ScheduledTaskRow)
            .where(
                ScheduledTaskRow.is_enabled.is_(True),
                ScheduledTaskRow.next_run_at.is_not(None),
                ScheduledTaskRow.next_run_at <= now,
                (ScheduledTaskRow.last_run_status.is_(None) | (ScheduledTaskRow.last_run_status != "running")),
            )
            .order_by(ScheduledTaskRow.next_run_at.asc(), ScheduledTaskRow.created_at.asc())
            .limit(limit)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_dict(row) for row in rows]

    async def mark_running(
        self,
        task_id: str,
        *,
        thread_id: str,
        run_id: str,
        run_at: datetime,
        next_run_at: datetime | None,
    ) -> bool:
        values = {
            "last_run_at": run_at,
            "last_run_status": "running",
            "last_run_thread_id": thread_id,
            "last_run_id": run_id,
            "next_run_at": next_run_at,
            "updated_at": datetime.now(UTC),
        }
        stmt = (
            update(ScheduledTaskRow)
            .where(
                ScheduledTaskRow.id == task_id,
                (ScheduledTaskRow.last_run_status.is_(None) | (ScheduledTaskRow.last_run_status != "running")),
            )
            .values(**values)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount != 0

    async def mark_finished(
        self,
        task_id: str,
        *,
        status: str,
        disable: bool = False,
        run_id: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "last_run_status": status,
            "updated_at": datetime.now(UTC),
        }
        if disable:
            values["is_enabled"] = False
            values["next_run_at"] = None
        stmt = update(ScheduledTaskRow).where(ScheduledTaskRow.id == task_id)
        if run_id is not None:
            stmt = stmt.where(ScheduledTaskRow.last_run_id == run_id)
        async with self._sf() as session:
            await session.execute(stmt.values(**values))
            await session.commit()


class MemoryScheduledTaskStore:
    """In-memory scheduled task store used when database.backend=memory."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskDict] = {}
        self._lock = asyncio.Lock()

    async def create(self, values: Mapping[str, Any]) -> TaskDict:
        async with self._lock:
            task = _copy_task(values)
            self._tasks[task["id"]] = task
            return _copy_task(task)

    async def get(self, task_id: str, *, user_id: str | None) -> TaskDict | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if user_id is not None and task.get("user_id") != user_id:
                return None
            return _copy_task(task)

    async def list(self, *, user_id: str | None) -> list[TaskDict]:
        async with self._lock:
            rows = [_copy_task(task) for task in self._tasks.values() if user_id is None or task.get("user_id") == user_id]
        return sorted(rows, key=lambda task: task["created_at"], reverse=True)

    async def update(self, task_id: str, values: Mapping[str, Any], *, user_id: str | None) -> TaskDict | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if user_id is not None and task.get("user_id") != user_id:
                return None
            task.update(values)
            task["updated_at"] = datetime.now(UTC)
            return _copy_task(task)

    async def delete(self, task_id: str, *, user_id: str | None) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if user_id is not None and task.get("user_id") != user_id:
                return False
            del self._tasks[task_id]
            return True

    async def list_due(self, *, now: datetime, limit: int = 20) -> list[TaskDict]:
        async with self._lock:
            rows = [_copy_task(task) for task in self._tasks.values() if task.get("is_enabled") and task.get("next_run_at") is not None and task["next_run_at"] <= now and task.get("last_run_status") != "running"]
        return sorted(rows, key=lambda task: (task["next_run_at"], task["created_at"]))[:limit]

    async def mark_running(
        self,
        task_id: str,
        *,
        thread_id: str,
        run_id: str,
        run_at: datetime,
        next_run_at: datetime | None,
    ) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.get("last_run_status") == "running":
                return False
            task.update(
                {
                    "last_run_at": run_at,
                    "last_run_status": "running",
                    "last_run_thread_id": thread_id,
                    "last_run_id": run_id,
                    "next_run_at": next_run_at,
                    "updated_at": datetime.now(UTC),
                }
            )
            return True

    async def mark_finished(
        self,
        task_id: str,
        *,
        status: str,
        disable: bool = False,
        run_id: str | None = None,
    ) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            if run_id is not None and task.get("last_run_id") != run_id:
                return
            task["last_run_status"] = status
            if disable:
                task["is_enabled"] = False
                task["next_run_at"] = None
            task["updated_at"] = datetime.now(UTC)
