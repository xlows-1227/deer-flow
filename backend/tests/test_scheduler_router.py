from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage

from app.gateway.app import create_app
from app.gateway.auth.models import User
from app.gateway.routers import scheduler
from app.gateway.scheduler import (
    ExecuteTaskResult,
    ScheduledTaskCreate,
    SchedulerService,
    _scheduled_run_outcome,
    _ScheduledTaskFailureTracker,
    calculate_next_run,
)
from deerflow.persistence.scheduled_task import MemoryScheduledTaskStore
from deerflow.persistence.scheduled_task_run import MemoryScheduledTaskRunStore
from deerflow.runtime import DisconnectMode, RunRecord, RunStatus

_USER_ID = UUID("11111111-1111-4111-8111-111111111111")


def _stable_user() -> User:
    return User(
        id=_USER_ID,
        email="scheduler-test@example.com",
        password_hash="x",
        system_role="user",
    )


def _make_app():
    app = make_authed_test_app(user_factory=_stable_user)
    app.state.scheduler_store = MemoryScheduledTaskStore()
    app.include_router(scheduler.router)
    return app


def _daily_payload(**overrides):
    payload = {
        "name": "Daily report",
        "prompt": "Summarize yesterday's incidents.",
        "repeat_type": "daily",
        "execution_time": "09:30",
        "timezone": "Asia/Shanghai",
        "day_of_week": None,
        "is_enabled": True,
        "model_name": None,
        "mode": "pro",
        "reasoning_effort": "medium",
    }
    payload.update(overrides)
    return payload


def test_scheduler_requires_authentication():
    app = FastAPI()
    app.state.scheduler_store = MemoryScheduledTaskStore()
    app.include_router(scheduler.router)

    with TestClient(app) as client:
        response = client.get("/api/scheduler/tasks")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_scheduler_task_crud_and_toggle():
    app = _make_app()

    with TestClient(app) as client:
        created = client.post("/api/scheduler/tasks", json=_daily_payload())
        assert created.status_code == 201
        task = created.json()
        assert task["name"] == "Daily report"
        assert task["timezone"] == "Asia/Shanghai"
        assert task["next_run_at"] is not None

        listed = client.get("/api/scheduler/tasks")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        updated = client.put(
            f"/api/scheduler/tasks/{task['id']}",
            json=_daily_payload(
                name="Weekly report",
                repeat_type="weekly",
                day_of_week=2,
                execution_time="18:00",
                mode="ultra",
                reasoning_effort="high",
            ),
        )
        assert updated.status_code == 200
        updated_task = updated.json()
        assert updated_task["repeat_type"] == "weekly"
        assert updated_task["day_of_week"] == 2
        assert updated_task["mode"] == "ultra"

        disabled = client.patch(f"/api/scheduler/tasks/{task['id']}/toggle")
        assert disabled.status_code == 200
        assert disabled.json()["is_enabled"] is False
        assert disabled.json()["next_run_at"] is None

        deleted = client.delete(f"/api/scheduler/tasks/{task['id']}")
        assert deleted.status_code == 204

        listed_again = client.get("/api/scheduler/tasks")
        assert listed_again.status_code == 200
        assert listed_again.json()["total"] == 0


def test_scheduler_requires_weekly_day():
    app = _make_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/scheduler/tasks",
            json=_daily_payload(repeat_type="weekly", day_of_week=None),
        )

    assert response.status_code == 422


def test_calculate_next_run_returns_utc_datetime():
    next_run = calculate_next_run("daily", "09:30", None)

    assert next_run is not None
    assert next_run.tzinfo is not None


def test_calculate_next_run_uses_task_timezone():
    next_run = calculate_next_run(
        "daily",
        "14:20",
        None,
        timezone="Asia/Shanghai",
        from_dt=datetime(2026, 6, 16, 6, 15, tzinfo=UTC),
    )

    assert next_run == datetime(2026, 6, 16, 6, 20, tzinfo=UTC)
    assert next_run.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%H:%M") == "14:20"


def test_calculate_next_run_rolls_daily_task_to_next_local_day():
    next_run = calculate_next_run(
        "daily",
        "14:20",
        None,
        timezone="Asia/Shanghai",
        from_dt=datetime(2026, 6, 16, 6, 21, tzinfo=UTC),
    )

    assert next_run == datetime(2026, 6, 17, 6, 20, tzinfo=UTC)


def test_list_tasks_backfills_timezone_for_legacy_task():
    app = _make_app()

    with TestClient(app) as client:
        created = client.post("/api/scheduler/tasks", json=_daily_payload())
        task_id = created.json()["id"]
        asyncio.run(
            app.state.scheduler_store.update(
                task_id,
                {"timezone": None},
                user_id=str(_USER_ID),
            )
        )

        listed = client.get(
            "/api/scheduler/tasks",
            headers={"X-Time-Zone": "Asia/Shanghai"},
        )

    assert listed.status_code == 200
    task = listed.json()["tasks"][0]
    assert task["timezone"] == "Asia/Shanghai"
    next_run = datetime.fromisoformat(task["next_run_at"])
    assert next_run.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%H:%M") == "09:30"


def test_scheduled_run_outcome_treats_tool_error_as_failure():
    tracker = _ScheduledTaskFailureTracker()
    tracker.on_tool_end(
        ToolMessage(
            content="Error: upstream request failed",
            tool_call_id="tool-call-1",
            name="http_request",
        )
    )
    record = RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id="lead_agent",
        status=RunStatus.success,
        on_disconnect=DisconnectMode.continue_,
    )

    status, error = _scheduled_run_outcome(record, tracker=tracker)

    assert status == "error"
    assert error == "Error: upstream request failed"


def test_scheduled_run_outcome_does_not_hide_worker_exception():
    tracker = _ScheduledTaskFailureTracker()
    record = RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id="lead_agent",
        status=RunStatus.success,
        on_disconnect=DisconnectMode.continue_,
    )

    status, error = _scheduled_run_outcome(
        record,
        tracker=tracker,
        worker_error=RuntimeError("publish end failed"),
    )

    assert status == "error"
    assert error == "publish end failed"


@pytest.mark.anyio
async def test_reconciled_running_task_can_execute_at_next_due_time():
    app = FastAPI()
    app.state.scheduler_store = MemoryScheduledTaskStore()
    app.state.scheduler_run_store = MemoryScheduledTaskRunStore()
    recovered_record = RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id="lead_agent",
        status=RunStatus.error,
        on_disconnect=DisconnectMode.continue_,
        error="Gateway restarted",
    )
    app.state.run_manager = SimpleNamespace(get=AsyncMock(return_value=recovered_record))
    service = SchedulerService(app)
    task = await service.create_task(
        ScheduledTaskCreate.model_validate(_daily_payload()),
        user_id=str(_USER_ID),
    )
    due_at = datetime(2026, 6, 22, 6, 20, tzinfo=UTC)
    await app.state.scheduler_store.mark_running(
        task.id,
        thread_id="thread-1",
        run_id="run-1",
        run_at=due_at,
        next_run_at=due_at,
    )
    await app.state.scheduler_run_store.create(
        {
            "task_id": task.id,
            "run_id": "run-1",
            "thread_id": "thread-1",
            "status": "running",
            "started_at": due_at,
            "is_automatic": True,
        }
    )

    with (
        patch("app.gateway.scheduler.datetime") as mock_datetime,
        patch.object(
            service,
            "execute_task",
            AsyncMock(return_value=ExecuteTaskResult(found=True)),
        ) as execute_task,
    ):
        mock_datetime.now.return_value = due_at
        await service.run_due_tasks_once()

    row = await app.state.scheduler_store.get(task.id, user_id=str(_USER_ID))
    assert row is not None
    assert row["last_run_status"] == "error"
    execute_task.assert_awaited_once_with(
        task.id,
        user_id=str(_USER_ID),
        automatic=True,
    )
    history = await app.state.scheduler_run_store.list_by_task(task.id)
    assert history[0]["status"] == "error"
    assert history[0]["error"] == "Gateway restarted"


@pytest.mark.anyio
async def test_stale_run_cannot_overwrite_new_scheduled_execution():
    store = MemoryScheduledTaskStore()
    task = {
        "id": "task-1",
        "user_id": str(_USER_ID),
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "last_run_status": None,
    }
    await store.create(task)
    now = datetime.now(UTC)
    await store.mark_running(
        "task-1",
        thread_id="thread-1",
        run_id="run-1",
        run_at=now,
        next_run_at=now,
    )
    await store.mark_finished("task-1", status="success", run_id="run-1")
    await store.mark_running(
        "task-1",
        thread_id="thread-2",
        run_id="run-2",
        run_at=now,
        next_run_at=now,
    )

    await store.mark_finished("task-1", status="error", run_id="run-1")

    current = await store.get("task-1", user_id=str(_USER_ID))
    assert current is not None
    assert current["last_run_id"] == "run-2"
    assert current["last_run_status"] == "running"


def test_gateway_app_mounts_scheduler_router():
    app = create_app()
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/scheduler/tasks" in paths
