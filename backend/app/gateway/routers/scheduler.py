"""Scheduled tasks API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.gateway.authz import require_auth
from app.gateway.scheduler import (
    ScheduledTaskCancelResponse,
    ScheduledTaskCreate,
    ScheduledTaskHistoryResponse,
    ScheduledTaskListResponse,
    ScheduledTaskResponse,
    ScheduledTaskRunResponse,
    ScheduledTaskUpdate,
    SchedulerService,
)

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


def _require_user_id(request: Request) -> str:
    auth = getattr(request.state, "auth", None)
    if auth is None or not auth.is_authenticated or auth.user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    if getattr(request.state, "auth_method", None) == "internal":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return str(auth.user.id)


def _service(request: Request) -> SchedulerService:
    if not hasattr(request.app.state, "scheduler_store"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Scheduler store not available")
    return SchedulerService(request.app)


def _client_timezone(request: Request) -> str | None:
    return request.headers.get("x-time-zone")


@router.post("/tasks", response_model=ScheduledTaskResponse, status_code=status.HTTP_201_CREATED)
@require_auth
async def create_task(data: ScheduledTaskCreate, request: Request) -> ScheduledTaskResponse:
    try:
        return await _service(request).create_task(data, user_id=_require_user_id(request))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/tasks", response_model=ScheduledTaskListResponse)
@require_auth
async def list_tasks(request: Request) -> ScheduledTaskListResponse:
    try:
        tasks = await _service(request).list_tasks(
            user_id=_require_user_id(request),
            client_timezone=_client_timezone(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ScheduledTaskListResponse(tasks=tasks, total=len(tasks))


@router.get("/tasks/{task_id}", response_model=ScheduledTaskResponse)
@require_auth
async def get_task(task_id: str, request: Request) -> ScheduledTaskResponse:
    task = await _service(request).get_task(task_id, user_id=_require_user_id(request))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scheduled task {task_id!r} not found")
    return task


@router.put("/tasks/{task_id}", response_model=ScheduledTaskResponse)
@require_auth
async def update_task(task_id: str, data: ScheduledTaskUpdate, request: Request) -> ScheduledTaskResponse:
    try:
        task = await _service(request).update_task(task_id, data, user_id=_require_user_id(request))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scheduled task {task_id!r} not found")
    return task


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_auth
async def delete_task(task_id: str, request: Request) -> None:
    deleted = await _service(request).delete_task(task_id, user_id=_require_user_id(request))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scheduled task {task_id!r} not found")


@router.patch("/tasks/{task_id}/toggle", response_model=ScheduledTaskResponse)
@require_auth
async def toggle_task(task_id: str, request: Request) -> ScheduledTaskResponse:
    task = await _service(request).toggle_task(task_id, user_id=_require_user_id(request))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scheduled task {task_id!r} not found")
    return task


@router.post("/tasks/{task_id}/run", response_model=ScheduledTaskRunResponse)
@require_auth
async def run_task(task_id: str, request: Request) -> ScheduledTaskRunResponse:
    result = await _service(request).execute_task(task_id, user_id=_require_user_id(request), automatic=False)
    if not result.found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scheduled task {task_id!r} not found")
    if result.thread_id is None or result.run_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.error_message or "Scheduled task did not start")
    return ScheduledTaskRunResponse(thread_id=result.thread_id, run_id=result.run_id)


@router.post("/tasks/{task_id}/cancel", response_model=ScheduledTaskCancelResponse)
@require_auth
async def cancel_task(task_id: str, request: Request) -> ScheduledTaskCancelResponse:
    result = await _service(request).cancel_running_execution(task_id, user_id=_require_user_id(request))
    if result.reason == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result.message)
    return result


@router.get("/tasks/{task_id}/history", response_model=ScheduledTaskHistoryResponse)
@require_auth
async def task_history(task_id: str, request: Request) -> ScheduledTaskHistoryResponse:
    result = await _service(request).task_history(task_id, user_id=_require_user_id(request))
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scheduled task {task_id!r} not found")
    return result
