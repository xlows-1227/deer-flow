export type ScheduledTaskRepeatType = "once" | "daily" | "weekly";
export type ScheduledTaskMode = "flash" | "thinking" | "pro" | "ultra";
export type ScheduledTaskReasoningEffort =
  | "minimal"
  | "low"
  | "medium"
  | "high";

export interface ScheduledTask {
  id: string;
  name: string;
  prompt: string;
  repeat_type: ScheduledTaskRepeatType;
  execution_time: string;
  timezone: string;
  day_of_week: number | null;
  is_enabled: boolean;
  model_name: string | null;
  mode: ScheduledTaskMode;
  reasoning_effort: ScheduledTaskReasoningEffort | null;
  last_run_at: string | null;
  last_run_status: "running" | "success" | "error" | "cancelled" | null;
  last_run_thread_id: string | null;
  last_run_id: string | null;
  next_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export type ScheduledTaskPayload = Pick<
  ScheduledTask,
  | "name"
  | "prompt"
  | "repeat_type"
  | "execution_time"
  | "timezone"
  | "day_of_week"
  | "is_enabled"
  | "model_name"
  | "mode"
  | "reasoning_effort"
>;

export interface ScheduledTaskListResponse {
  tasks: ScheduledTask[];
  total: number;
}

export interface ScheduledTaskRunResponse {
  thread_id: string;
  run_id: string;
}

export interface ScheduledTaskCancelResponse {
  ok: boolean;
  reason: string;
  message: string;
}

export interface ScheduledTaskRunHistory {
  run_id: string;
  thread_id: string | null;
  status: "running" | "success" | "error" | "cancelled";
  created_at: string;
  updated_at: string;
  error: string | null;
}

export interface ScheduledTaskHistoryResponse {
  task: ScheduledTask;
  runs: ScheduledTaskRunHistory[];
}
