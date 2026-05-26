"""Scheduled task models, service, and background loop for the Gateway."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app.gateway.services import (
    build_run_config,
    merge_run_context_overrides,
    normalize_input,
    resolve_agent_factory,
)
from deerflow.config.app_config import get_app_config
from deerflow.runtime import DisconnectMode, RunContext, RunRecord, RunStatus, run_agent
from deerflow.runtime.user_context import reset_current_user, set_current_user

logger = logging.getLogger(__name__)

RepeatType = Literal["once", "daily", "weekly"]
RunMode = Literal["flash", "thinking", "pro", "ultra"]
ReasoningEffort = Literal["minimal", "low", "medium", "high"]

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SCHEDULER_INTERVAL_SECONDS = 30.0


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _now_local() -> datetime:
    return datetime.now(_local_tz())


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=_local_tz())
    return value.astimezone(UTC)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return None


def _parse_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute), tzinfo=_local_tz())


def calculate_next_run(
    repeat_type: str,
    execution_time: str,
    day_of_week: int | None,
    *,
    from_dt: datetime | None = None,
) -> datetime | None:
    """Return the next run time in UTC.

    The UI captures wall-clock time in the user's/server's local timezone.
    Persisting UTC keeps comparisons stable while browsers still render the
    correct local time from the ISO response.
    """
    base = from_dt.astimezone(_local_tz()) if from_dt else _now_local()
    run_time = _parse_time(execution_time)
    today = base.date()

    if repeat_type == "once":
        candidate = datetime.combine(today, run_time)
        if candidate <= base:
            return _to_utc(base)
        return _to_utc(candidate)

    if repeat_type == "daily":
        candidate = datetime.combine(today, run_time)
        if candidate <= base:
            candidate += timedelta(days=1)
        return _to_utc(candidate)

    if repeat_type == "weekly" and day_of_week is not None:
        days_ahead = (day_of_week - today.weekday() + 7) % 7
        candidate = datetime.combine(today + timedelta(days=days_ahead), run_time)
        if candidate <= base:
            candidate += timedelta(days=7)
        return _to_utc(candidate)

    return None


def _default_reasoning_effort(mode: str) -> str | None:
    if mode == "ultra":
        return "high"
    if mode == "pro":
        return "medium"
    if mode == "thinking":
        return "low"
    return None


def _status_for_record(record: RunRecord) -> str:
    if record.status == RunStatus.success:
        return "success"
    if record.status == RunStatus.interrupted:
        return "cancelled"
    return "error"


class ScheduledTaskBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    prompt: str = Field(..., min_length=1)
    repeat_type: RepeatType
    execution_time: str = Field(..., description="HH:MM in local time")
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    is_enabled: bool = True
    model_name: str | None = None
    mode: RunMode = "pro"
    reasoning_effort: ReasoningEffort | None = None

    @field_validator("execution_time")
    @classmethod
    def _validate_execution_time(cls, value: str) -> str:
        if not _TIME_RE.match(value):
            raise ValueError("execution_time must be HH:MM in 24-hour time")
        return value

    @field_validator("name", "prompt")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def _validate_weekly_day(self):
        if self.repeat_type == "weekly" and self.day_of_week is None:
            raise ValueError("day_of_week is required when repeat_type is weekly")
        if self.repeat_type != "weekly":
            self.day_of_week = None
        if self.reasoning_effort is None:
            self.reasoning_effort = _default_reasoning_effort(self.mode)
        return self


class ScheduledTaskCreate(ScheduledTaskBase):
    pass


class ScheduledTaskUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    prompt: str | None = Field(default=None, min_length=1)
    repeat_type: RepeatType | None = None
    execution_time: str | None = None
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    is_enabled: bool | None = None
    model_name: str | None = None
    mode: RunMode | None = None
    reasoning_effort: ReasoningEffort | None = None

    @field_validator("execution_time")
    @classmethod
    def _validate_execution_time(cls, value: str | None) -> str | None:
        if value is not None and not _TIME_RE.match(value):
            raise ValueError("execution_time must be HH:MM in 24-hour time")
        return value

    @field_validator("name", "prompt")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class ScheduledTaskResponse(ScheduledTaskBase):
    id: str
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    last_run_thread_id: str | None = None
    last_run_id: str | None = None
    next_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ScheduledTaskListResponse(BaseModel):
    tasks: list[ScheduledTaskResponse]
    total: int


class ScheduledTaskRunResponse(BaseModel):
    thread_id: str
    run_id: str


class ScheduledTaskCancelResponse(BaseModel):
    ok: bool
    reason: str
    message: str


class ScheduledRunSummary(BaseModel):
    run_id: str
    thread_id: str
    status: str
    created_at: str = ""
    updated_at: str = ""
    error: str | None = None


class ScheduledTaskHistoryResponse(BaseModel):
    task: ScheduledTaskResponse
    runs: list[ScheduledRunSummary] = Field(default_factory=list)


@dataclass
class ExecuteTaskResult:
    found: bool
    thread_id: str | None = None
    run_id: str | None = None
    error_message: str | None = None


class SchedulerService:
    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._store = app.state.scheduler_store

    def _run_context(self) -> RunContext:
        return RunContext(
            checkpointer=self._app.state.checkpointer,
            store=getattr(self._app.state, "store", None),
            event_store=getattr(self._app.state, "run_event_store", None),
            run_events_config=getattr(self._app.state, "run_events_config", None),
            thread_store=self._app.state.thread_store,
            app_config=get_app_config(),
        )

    @staticmethod
    def _response(task: dict[str, Any]) -> ScheduledTaskResponse:
        return ScheduledTaskResponse.model_validate(task)

    @staticmethod
    def _validate_model_name(model_name: str | None) -> None:
        if not model_name:
            return
        if get_app_config().get_model_config(model_name) is None:
            raise ValueError(f"Model {model_name!r} is not in the configured model allowlist")

    async def create_task(self, data: ScheduledTaskCreate, *, user_id: str) -> ScheduledTaskResponse:
        self._validate_model_name(data.model_name)
        now = datetime.now(UTC)
        values = data.model_dump()
        values.update(
            {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "next_run_at": calculate_next_run(data.repeat_type, data.execution_time, data.day_of_week) if data.is_enabled else None,
                "last_run_at": None,
                "last_run_status": None,
                "last_run_thread_id": None,
                "last_run_id": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        return self._response(await self._store.create(values))

    async def list_tasks(self, *, user_id: str) -> list[ScheduledTaskResponse]:
        return [self._response(row) for row in await self._store.list(user_id=user_id)]

    async def get_task(self, task_id: str, *, user_id: str) -> ScheduledTaskResponse | None:
        row = await self._store.get(task_id, user_id=user_id)
        return self._response(row) if row is not None else None

    async def update_task(self, task_id: str, data: ScheduledTaskUpdate, *, user_id: str) -> ScheduledTaskResponse | None:
        current = await self._store.get(task_id, user_id=user_id)
        if current is None:
            return None

        updates = data.model_dump(exclude_unset=True)
        if "model_name" in updates:
            self._validate_model_name(updates["model_name"])

        merged = {**current, **updates}
        if merged["repeat_type"] == "weekly" and merged.get("day_of_week") is None:
            raise ValueError("day_of_week is required when repeat_type is weekly")
        if merged["repeat_type"] != "weekly":
            updates["day_of_week"] = None
            merged["day_of_week"] = None
        if "mode" in updates and "reasoning_effort" not in updates:
            updates["reasoning_effort"] = _default_reasoning_effort(merged["mode"])
            merged["reasoning_effort"] = updates["reasoning_effort"]

        updates["next_run_at"] = (
            calculate_next_run(
                merged["repeat_type"],
                merged["execution_time"],
                merged.get("day_of_week"),
            )
            if merged["is_enabled"]
            else None
        )
        row = await self._store.update(task_id, updates, user_id=user_id)
        return self._response(row) if row is not None else None

    async def delete_task(self, task_id: str, *, user_id: str) -> bool:
        return await self._store.delete(task_id, user_id=user_id)

    async def toggle_task(self, task_id: str, *, user_id: str) -> ScheduledTaskResponse | None:
        current = await self._store.get(task_id, user_id=user_id)
        if current is None:
            return None
        enabled = not bool(current["is_enabled"])
        next_run_at = (
            calculate_next_run(
                current["repeat_type"],
                current["execution_time"],
                current.get("day_of_week"),
            )
            if enabled
            else None
        )
        row = await self._store.update(task_id, {"is_enabled": enabled, "next_run_at": next_run_at}, user_id=user_id)
        return self._response(row) if row is not None else None

    def _task_context(self, task: dict[str, Any], thread_id: str, user_id: str) -> dict[str, Any]:
        mode = task.get("mode") or "pro"
        reasoning_effort = task.get("reasoning_effort") or _default_reasoning_effort(mode)
        return {
            "model_name": task.get("model_name"),
            "mode": mode,
            "thinking_enabled": mode != "flash",
            "is_plan_mode": mode in ("pro", "ultra"),
            "subagent_enabled": mode == "ultra",
            "reasoning_effort": reasoning_effort,
            "thread_id": thread_id,
            "user_id": user_id,
        }

    async def execute_task(self, task_id: str, *, user_id: str, automatic: bool = False) -> ExecuteTaskResult:
        task = await self._store.get(task_id, user_id=user_id)
        if task is None:
            return ExecuteTaskResult(found=False)
        if task.get("last_run_status") == "running":
            return ExecuteTaskResult(found=True, error_message="Task is already running")

        try:
            self._validate_model_name(task.get("model_name"))
        except ValueError as exc:
            await self._store.mark_finished(task_id, status="error")
            return ExecuteTaskResult(found=True, error_message=str(exc))

        thread_id = str(uuid.uuid4())
        metadata = {
            "source": "scheduled_task",
            "scheduled_task_id": task_id,
            "scheduled_task_name": task["name"],
        }
        context = self._task_context(task, thread_id, user_id)
        raw_input = {
            "messages": [
                {
                    "role": "user",
                    "content": task["prompt"],
                    "additional_kwargs": metadata,
                }
            ]
        }
        request_config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 1000,
        }
        config = build_run_config(thread_id, request_config, metadata, assistant_id="lead_agent")
        merge_run_context_overrides(config, context)
        config.setdefault("context", {}).update({"thread_id": thread_id, "user_id": user_id})

        token = set_current_user(SimpleNamespace(id=user_id))
        try:
            run_manager = self._app.state.run_manager
            record = await run_manager.create_or_reject(
                thread_id,
                "lead_agent",
                on_disconnect=DisconnectMode.continue_,
                metadata=metadata,
                kwargs={"input": raw_input, "config": request_config},
                multitask_strategy="reject",
                model_name=task.get("model_name"),
            )

            existing_thread = await self._app.state.thread_store.get(thread_id, user_id=user_id)
            if existing_thread is None:
                await self._app.state.thread_store.create(
                    thread_id,
                    assistant_id="lead_agent",
                    user_id=user_id,
                    display_name=task["name"],
                    metadata=metadata,
                )

            run_at = datetime.now(UTC)
            if automatic:
                next_run_at = (
                    calculate_next_run(
                        task["repeat_type"],
                        task["execution_time"],
                        task.get("day_of_week"),
                        from_dt=run_at + timedelta(seconds=1),
                    )
                    if task["repeat_type"] != "once"
                    else None
                )
            else:
                next_run_at = _parse_datetime(task.get("next_run_at"))

            marked = await self._store.mark_running(
                task_id,
                thread_id=thread_id,
                run_id=record.run_id,
                run_at=run_at,
                next_run_at=next_run_at,
            )
            if not marked:
                await run_manager.cancel(record.run_id)
                return ExecuteTaskResult(found=True, error_message="Task is already running")

            record.task = asyncio.create_task(
                self._run_task_worker(
                    task_id,
                    task,
                    record,
                    user_id=user_id,
                    graph_input=normalize_input(raw_input),
                    config=config,
                    automatic=automatic,
                )
            )
            return ExecuteTaskResult(found=True, thread_id=thread_id, run_id=record.run_id)
        except HTTPException as exc:
            return ExecuteTaskResult(found=True, error_message=str(exc.detail))
        except Exception as exc:  # noqa: BLE001 - request boundary and scheduler boundary
            logger.exception("Failed to start scheduled task %s", task_id)
            await self._store.mark_finished(task_id, status="error")
            return ExecuteTaskResult(found=True, error_message=str(exc))
        finally:
            reset_current_user(token)

    async def _run_task_worker(
        self,
        task_id: str,
        task: dict[str, Any],
        record: RunRecord,
        *,
        user_id: str,
        graph_input: dict[str, Any],
        config: dict[str, Any],
        automatic: bool,
    ) -> None:
        token = set_current_user(SimpleNamespace(id=user_id))
        try:
            await run_agent(
                self._app.state.stream_bridge,
                self._app.state.run_manager,
                record,
                ctx=self._run_context(),
                agent_factory=resolve_agent_factory("lead_agent"),
                graph_input=graph_input,
                config=config,
                stream_modes=["values", "messages-tuple"],
                stream_subgraphs=True,
            )
        finally:
            reset_current_user(token)
            await self._store.mark_finished(
                task_id,
                status=_status_for_record(record),
                disable=automatic and task.get("repeat_type") == "once",
            )

    async def cancel_running_execution(self, task_id: str, *, user_id: str) -> ScheduledTaskCancelResponse:
        task = await self._store.get(task_id, user_id=user_id)
        if task is None:
            return ScheduledTaskCancelResponse(ok=False, reason="not_found", message="Scheduled task not found")
        if task.get("last_run_status") != "running" or not task.get("last_run_id"):
            return ScheduledTaskCancelResponse(ok=True, reason="not_running", message="Task is not running")

        record = await self._app.state.run_manager.get(task["last_run_id"], user_id=user_id)
        if record is None or record.store_only:
            await self._store.mark_finished(task_id, status="cancelled")
            return ScheduledTaskCancelResponse(ok=False, reason="not_active", message="Run is no longer active on this worker")

        cancelled = await self._app.state.run_manager.cancel(record.run_id)
        if cancelled:
            await self._store.mark_finished(task_id, status="cancelled")
            return ScheduledTaskCancelResponse(ok=True, reason="cancelled", message="Task run cancelled")
        return ScheduledTaskCancelResponse(ok=False, reason="not_cancellable", message="Task run is not cancellable")

    async def task_history(self, task_id: str, *, user_id: str) -> ScheduledTaskHistoryResponse | None:
        task = await self.get_task(task_id, user_id=user_id)
        if task is None:
            return None
        runs: list[ScheduledRunSummary] = []
        if task.last_run_id:
            record = await self._app.state.run_manager.get(task.last_run_id, user_id=user_id)
            if record is not None:
                runs.append(
                    ScheduledRunSummary(
                        run_id=record.run_id,
                        thread_id=record.thread_id,
                        status=record.status.value,
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                        error=record.error,
                    )
                )
        return ScheduledTaskHistoryResponse(task=task, runs=runs)

    async def run_due_tasks_once(self) -> None:
        due = await self._store.list_due(now=datetime.now(UTC), limit=20)
        for task in due:
            user_id = task.get("user_id")
            if not user_id:
                logger.warning("Skipping scheduled task %s without user_id", task.get("id"))
                continue
            result = await self.execute_task(task["id"], user_id=str(user_id), automatic=True)
            if result.error_message:
                logger.warning("Scheduled task %s did not start: %s", task["id"], result.error_message)


async def scheduler_loop(app: FastAPI, *, interval_seconds: float = _SCHEDULER_INTERVAL_SECONDS) -> None:
    service = SchedulerService(app)
    while True:
        try:
            await service.run_due_tasks_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled task loop iteration failed")
        await asyncio.sleep(interval_seconds)


def start_scheduler_loop(app: FastAPI) -> None:
    task = getattr(app.state, "scheduler_loop_task", None)
    if task is not None and not task.done():
        return
    app.state.scheduler_loop_task = asyncio.create_task(scheduler_loop(app), name="deerflow-scheduler-loop")
    logger.info("Scheduled task loop started")


async def stop_scheduler_loop(app: FastAPI) -> None:
    task = getattr(app.state, "scheduler_loop_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    app.state.scheduler_loop_task = None
    logger.info("Scheduled task loop stopped")
