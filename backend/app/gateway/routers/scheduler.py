"""Scheduled tasks API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.gateway.deps import get_current_user
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


async def _require_user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "id", None) is not None:
        return str(user.id)
    user_id = await get_current_user(request)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return str(user_id)


def _service(request: Request) -> SchedulerService:
    if not hasattr(request.app.state, "scheduler_store"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Scheduler store not available")
    return SchedulerService(request.app)


@router.post("/tasks", response_model=ScheduledTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(data: ScheduledTaskCreate, request: Request) -> ScheduledTaskResponse:
    try:
        return await _service(request).create_task(data, user_id=await _require_user_id(request))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/tasks", response_model=ScheduledTaskListResponse)
async def list_tasks(request: Request) -> ScheduledTaskListResponse:
    tasks = await _service(request).list_tasks(user_id=await _require_user_id(request))
    return ScheduledTaskListResponse(tasks=tasks, total=len(tasks))


@router.get("/tasks/{task_id}", response_model=ScheduledTaskResponse)
async def get_task(task_id: str, request: Request) -> ScheduledTaskResponse:
    task = await _service(request).get_task(task_id, user_id=await _require_user_id(request))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scheduled task {task_id!r} not found")
    return task


@router.put("/tasks/{task_id}", response_model=ScheduledTaskResponse)
async def update_task(task_id: str, data: ScheduledTaskUpdate, request: Request) -> ScheduledTaskResponse:
    try:
        task = await _service(request).update_task(task_id, data, user_id=await _require_user_id(request))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scheduled task {task_id!r} not found")
    return task


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str, request: Request) -> None:
    deleted = await _service(request).delete_task(task_id, user_id=await _require_user_id(request))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scheduled task {task_id!r} not found")


@router.patch("/tasks/{task_id}/toggle", response_model=ScheduledTaskResponse)
async def toggle_task(task_id: str, request: Request) -> ScheduledTaskResponse:
    task = await _service(request).toggle_task(task_id, user_id=await _require_user_id(request))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scheduled task {task_id!r} not found")
    return task


@router.post("/tasks/{task_id}/run", response_model=ScheduledTaskRunResponse)
async def run_task(task_id: str, request: Request) -> ScheduledTaskRunResponse:
    result = await _service(request).execute_task(task_id, user_id=await _require_user_id(request), automatic=False)
    if not result.found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scheduled task {task_id!r} not found")
    if result.thread_id is None or result.run_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.error_message or "Scheduled task did not start")
    return ScheduledTaskRunResponse(thread_id=result.thread_id, run_id=result.run_id)


@router.post("/tasks/{task_id}/cancel", response_model=ScheduledTaskCancelResponse)
async def cancel_task(task_id: str, request: Request) -> ScheduledTaskCancelResponse:
    result = await _service(request).cancel_running_execution(task_id, user_id=await _require_user_id(request))
    if result.reason == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result.message)
    return result


@router.get("/tasks/{task_id}/history", response_model=ScheduledTaskHistoryResponse)
async def task_history(task_id: str, request: Request) -> ScheduledTaskHistoryResponse:
    result = await _service(request).task_history(task_id, user_id=await _require_user_id(request))
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scheduled task {task_id!r} not found")
    return result
