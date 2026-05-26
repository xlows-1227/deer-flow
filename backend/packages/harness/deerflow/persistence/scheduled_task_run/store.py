"""Scheduled task run persistence implementations."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.scheduled_task_run.model import ScheduledTaskRunRow
from deerflow.utils.time import coerce_iso

TaskRunDict = dict[str, Any]


def _copy_run(run: Mapping[str, Any]) -> TaskRunDict:
    return dict(run)


def _normalize_datetime(value: Any) -> Any:
    if isinstance(value, datetime):
        return coerce_iso(value)
    return value


def _row_to_dict(row: ScheduledTaskRunRow) -> TaskRunDict:
    data = row.to_dict()
    for key in ("started_at", "finished_at", "created_at"):
        data[key] = _normalize_datetime(data.get(key))
    return data


class ScheduledTaskRunRepository:
    """SQLAlchemy-backed scheduled task run store."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, values: Mapping[str, Any]) -> TaskRunDict:
        row = ScheduledTaskRunRow(**dict(values))
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_dict(row)

    async def list_by_task(self, task_id: str, *, limit: int = 50) -> list[TaskRunDict]:
        stmt = (
            select(ScheduledTaskRunRow)
            .where(ScheduledTaskRunRow.task_id == task_id)
            .order_by(ScheduledTaskRunRow.started_at.desc())
            .limit(limit)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_dict(row) for row in rows]

    async def update_status(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: datetime | None = None,
        error: str | None = None,
    ) -> TaskRunDict | None:
        async with self._sf() as session:
            result = await session.execute(
                select(ScheduledTaskRunRow).where(ScheduledTaskRunRow.run_id == run_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            row.status = status
            if finished_at is not None:
                row.finished_at = finished_at
            if error is not None:
                row.error = error
            await session.commit()
            await session.refresh(row)
            return _row_to_dict(row)


class MemoryScheduledTaskRunStore:
    """In-memory scheduled task run store used when database.backend=memory."""

    def __init__(self) -> None:
        self._runs: dict[int, TaskRunDict] = {}
        self._lock = asyncio.Lock()
        self._next_id = 1

    async def create(self, values: Mapping[str, Any]) -> TaskRunDict:
        async with self._lock:
            run = _copy_run(values)
            run["id"] = self._next_id
            self._next_id += 1
            run["created_at"] = datetime.now(UTC)
            self._runs[run["id"]] = run
            return _copy_run(run)

    async def list_by_task(self, task_id: str, *, limit: int = 50) -> list[TaskRunDict]:
        async with self._lock:
            rows = [
                _copy_run(run)
                for run in self._runs.values()
                if run.get("task_id") == task_id
            ]
        rows.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        return rows[:limit]

    async def update_status(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: datetime | None = None,
        error: str | None = None,
    ) -> TaskRunDict | None:
        async with self._lock:
            for run in self._runs.values():
                if run.get("run_id") == run_id:
                    run["status"] = status
                    if finished_at is not None:
                        run["finished_at"] = finished_at
                    if error is not None:
                        run["error"] = error
                    return _copy_run(run)
        return None
