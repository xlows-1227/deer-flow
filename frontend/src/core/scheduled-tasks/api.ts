import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  ScheduledTask,
  ScheduledTaskCancelResponse,
  ScheduledTaskListResponse,
  ScheduledTaskPayload,
  ScheduledTaskRunResponse,
} from "./types";

async function parseError(response: Response): Promise<string> {
  const data = await response.json().catch(() => null);
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (detail?.message) return String(detail.message);
  return `HTTP ${response.status}: ${response.statusText}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getBackendBaseURL()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function listScheduledTasks(): Promise<ScheduledTaskListResponse> {
  return request<ScheduledTaskListResponse>("/api/scheduler/tasks");
}

export function createScheduledTask(
  payload: ScheduledTaskPayload,
): Promise<ScheduledTask> {
  return request<ScheduledTask>("/api/scheduler/tasks", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateScheduledTask(
  taskId: string,
  payload: ScheduledTaskPayload,
): Promise<ScheduledTask> {
  return request<ScheduledTask>(
    `/api/scheduler/tasks/${encodeURIComponent(taskId)}`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
  );
}

export function deleteScheduledTask(taskId: string): Promise<void> {
  return request<void>(`/api/scheduler/tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
}

export function toggleScheduledTask(taskId: string): Promise<ScheduledTask> {
  return request<ScheduledTask>(
    `/api/scheduler/tasks/${encodeURIComponent(taskId)}/toggle`,
    {
      method: "PATCH",
    },
  );
}

export function runScheduledTask(
  taskId: string,
): Promise<ScheduledTaskRunResponse> {
  return request<ScheduledTaskRunResponse>(
    `/api/scheduler/tasks/${encodeURIComponent(taskId)}/run`,
    {
      method: "POST",
    },
  );
}

export function cancelScheduledTask(
  taskId: string,
): Promise<ScheduledTaskCancelResponse> {
  return request<ScheduledTaskCancelResponse>(
    `/api/scheduler/tasks/${encodeURIComponent(taskId)}/cancel`,
    {
      method: "POST",
    },
  );
}
