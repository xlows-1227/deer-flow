from __future__ import annotations

from uuid import UUID

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.app import create_app
from app.gateway.auth.models import User
from app.gateway.routers import scheduler
from app.gateway.scheduler import calculate_next_run
from deerflow.persistence.scheduled_task import MemoryScheduledTaskStore

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
        "day_of_week": None,
        "is_enabled": True,
        "model_name": None,
        "mode": "pro",
        "reasoning_effort": "medium",
    }
    payload.update(overrides)
    return payload


def test_scheduler_task_crud_and_toggle():
    app = _make_app()

    with TestClient(app) as client:
        created = client.post("/api/scheduler/tasks", json=_daily_payload())
        assert created.status_code == 201
        task = created.json()
        assert task["name"] == "Daily report"
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


def test_gateway_app_mounts_scheduler_router():
    app = create_app()
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/scheduler/tasks" in paths
