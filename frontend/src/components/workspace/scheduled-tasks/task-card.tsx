"use client";

import {
  BanIcon,
  CalendarClockIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronUpIcon,
  ClockIcon,
  EyeIcon,
  HistoryIcon,
  LoaderCircleIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  RotateCwIcon,
  StopCircleIcon,
  Trash2Icon,
  ZapIcon,
} from "lucide-react";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getTaskHistory } from "@/core/scheduled-tasks/api";
import type {
  ScheduledTask,
  ScheduledTaskRunHistory,
} from "@/core/scheduled-tasks/types";
import { cn } from "@/lib/utils";

const repeatLabels: Record<string, string> = {
  once: "一次",
  daily: "每天",
  weekly: "每周",
};

const weekdayLabels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

function formatDateTime(value: string | null, timezone?: string) {
  if (!value) return "暂无";
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    ...(timezone ? { timeZone: timezone } : {}),
  });
}

function scheduleLabel(task: ScheduledTask) {
  if (task.repeat_type === "weekly" && task.day_of_week != null) {
    return `${weekdayLabels[task.day_of_week]} ${task.execution_time}`;
  }
  return `${repeatLabels[task.repeat_type]} ${task.execution_time}`;
}

function statusBadge(task: ScheduledTask) {
  if (task.last_run_status === "running") {
    return {
      icon: <LoaderCircleIcon className="size-3 animate-spin" />,
      label: "运行中",
      className: "border-amber-200 bg-amber-50 text-amber-700",
    };
  }
  if (task.last_run_status === "success") {
    return {
      icon: <CheckCircle2Icon className="size-3" />,
      label: "成功",
      className: "border-emerald-200 bg-emerald-50 text-emerald-700",
    };
  }
  if (task.last_run_status === "error") {
    return {
      icon: <BanIcon className="size-3" />,
      label: "失败",
      className: "border-red-200 bg-red-50 text-red-700",
    };
  }
  if (task.last_run_status === "cancelled") {
    return {
      icon: <StopCircleIcon className="size-3" />,
      label: "已取消",
      className: "border-slate-200 bg-slate-100 text-slate-600",
    };
  }
  return {
    icon: <ClockIcon className="size-3" />,
    label: "未执行",
    className: "border-slate-200 bg-white text-slate-500",
  };
}

function runStatusBadge(status: ScheduledTaskRunHistory["status"]) {
  if (status === "running") {
    return {
      icon: <LoaderCircleIcon className="size-3 animate-spin" />,
      label: "运行中",
      className: "text-amber-600",
    };
  }
  if (status === "success") {
    return {
      icon: <CheckCircle2Icon className="size-3" />,
      label: "成功",
      className: "text-emerald-600",
    };
  }
  if (status === "error") {
    return {
      icon: <BanIcon className="size-3" />,
      label: "失败",
      className: "text-red-600",
    };
  }
  return {
    icon: <StopCircleIcon className="size-3" />,
    label: "已取消",
    className: "text-slate-500",
  };
}

export function ScheduledTaskCard({
  task,
  busy,
  cancelling,
  onRun,
  onCancel,
  onToggle,
  onEdit,
  onDelete,
  onOpen,
}: {
  task: ScheduledTask;
  busy?: boolean;
  cancelling?: boolean;
  onRun: (task: ScheduledTask) => void;
  onCancel: (task: ScheduledTask) => void;
  onToggle: (task: ScheduledTask) => void;
  onEdit: (task: ScheduledTask) => void;
  onDelete: (task: ScheduledTask) => void;
  onOpen: (task: ScheduledTask) => void;
}) {
  const status = statusBadge(task);
  const running = task.last_run_status === "running" || busy;
  const canOpen = Boolean(task.last_run_thread_id);

  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyRuns, setHistoryRuns] = useState<ScheduledTaskRunHistory[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const result = await getTaskHistory(task.id);
      setHistoryRuns(result.runs);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载执行历史失败");
    } finally {
      setHistoryLoading(false);
    }
  }, [task.id]);

  const toggleHistory = useCallback(() => {
    if (!historyOpen && historyRuns.length === 0) {
      void loadHistory();
    }
    setHistoryOpen((open) => !open);
  }, [historyOpen, historyRuns.length, loadHistory]);

  return (
    <article
      className={cn(
        "flex min-h-[260px] flex-col rounded-lg border bg-white shadow-xs transition",
        running
          ? "border-amber-200 shadow-amber-100"
          : "border-slate-200 hover:border-slate-300",
        !task.is_enabled && "bg-slate-50/80 opacity-75",
      )}
    >
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 p-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <CalendarClockIcon className="size-4 shrink-0 text-slate-500" />
            <h3 className="truncate text-sm font-semibold text-slate-950">
              {task.name}
            </h3>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <Badge variant="outline" className={status.className}>
              {status.icon}
              {status.label}
            </Badge>
            <Badge variant="outline" className="border-slate-200 bg-slate-50">
              {task.repeat_type === "once" ? (
                <ZapIcon className="size-3" />
              ) : (
                <RotateCwIcon className="size-3" />
              )}
              {repeatLabels[task.repeat_type]}
            </Badge>
            <Badge variant="outline" className="border-slate-200 bg-slate-50">
              {task.mode}
            </Badge>
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="icon"
          className={cn(
            "size-8 rounded-full",
            task.is_enabled
              ? "border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
              : "border-slate-200 bg-white text-slate-500",
          )}
          onClick={() => onToggle(task)}
          title={task.is_enabled ? "暂停任务" : "启用任务"}
          aria-label={task.is_enabled ? "暂停任务" : "启用任务"}
        >
          {task.is_enabled ? (
            <PauseIcon className="size-3.5" />
          ) : (
            <PlayIcon className="size-3.5" />
          )}
        </Button>
      </div>

      <div className="flex flex-1 flex-col gap-3 p-4">
        <p className="line-clamp-3 text-sm leading-6 text-slate-600">
          {task.prompt}
        </p>

        <div className="mt-auto grid gap-2 text-xs text-slate-500">
          <div className="flex items-center justify-between gap-3">
            <span>计划</span>
            <span className="font-medium text-slate-800">
              {scheduleLabel(task)}
            </span>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span>下次运行</span>
            <span className="font-medium text-slate-800">
              {task.is_enabled
                ? formatDateTime(task.next_run_at, task.timezone)
                : "已暂停"}
            </span>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span>上次运行</span>
            <span className="font-medium text-slate-800">
              {formatDateTime(task.last_run_at, task.timezone)}
            </span>
          </div>
        </div>

        {/* Execution history */}
        <div className="mt-1">
          <button
            type="button"
            onClick={toggleHistory}
            className="flex w-full items-center justify-between rounded-md border border-slate-100 bg-slate-50 px-3 py-2 text-xs text-slate-600 transition-colors hover:bg-slate-100"
          >
            <span className="flex items-center gap-1.5">
              <HistoryIcon className="size-3.5" />
              执行历史
              {historyRuns.length > 0 && (
                <span className="rounded-full bg-slate-200 px-1.5 py-0.5 text-[10px] text-slate-600">
                  {historyRuns.length}
                </span>
              )}
            </span>
            {historyOpen ? (
              <ChevronUpIcon className="size-3.5" />
            ) : (
              <ChevronDownIcon className="size-3.5" />
            )}
          </button>

          {historyOpen && (
            <div className="mt-1 max-h-[280px] overflow-y-auto rounded-md border border-slate-100 bg-slate-50/50">
              {historyLoading ? (
                <div className="flex items-center justify-center py-4">
                  <LoaderCircleIcon className="size-4 animate-spin text-slate-400" />
                </div>
              ) : historyRuns.length === 0 ? (
                <div className="py-3 text-center text-xs text-slate-400">
                  暂无执行记录
                </div>
              ) : (
                <>
                  <ul className="divide-y divide-slate-100">
                    {historyRuns.map((run) => {
                      const runStatus = runStatusBadge(run.status);
                      return (
                        <li
                          key={run.run_id}
                          className="flex items-center justify-between gap-2 px-3 py-2.5"
                        >
                          <div className="flex min-w-0 flex-col gap-0.5">
                            <div className="flex items-center gap-1.5 text-xs">
                              <span className={runStatus.className}>
                                {runStatus.icon}
                              </span>
                              <span className="font-medium text-slate-700">
                                {runStatus.label}
                              </span>
                              {run.thread_id && (
                                <span className="text-[10px] text-slate-400">
                                  ·
                                </span>
                              )}
                              {run.thread_id && (
                                <button
                                  type="button"
                                  onClick={() =>
                                    onOpen({
                                      ...task,
                                      last_run_thread_id: run.thread_id,
                                    })
                                  }
                                  className="inline-flex items-center gap-0.5 text-[10px] text-indigo-600 hover:text-indigo-700 hover:underline"
                                >
                                  <EyeIcon className="size-2.5" />
                                  查看对话
                                </button>
                              )}
                            </div>
                            {run.error && (
                              <p className="truncate text-[10px] text-red-500">
                                {run.error}
                              </p>
                            )}
                          </div>
                          <span className="shrink-0 text-[10px] text-slate-400">
                            {formatDateTime(run.created_at, task.timezone)}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                  {historyRuns.length > 5 && (
                    <div className="border-t border-slate-100 px-3 py-1.5 text-center text-[10px] text-slate-400">
                      共 {historyRuns.length} 条记录，滚动查看更多
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 border-t border-slate-100 p-3">
        {running ? (
          <Button
            type="button"
            variant="outline"
            className="h-9 flex-1 border-red-200 text-red-600 hover:bg-red-50"
            disabled={cancelling}
            onClick={() => onCancel(task)}
          >
            {cancelling ? (
              <LoaderCircleIcon className="size-4 animate-spin" />
            ) : (
              <StopCircleIcon className="size-4" />
            )}
            取消
          </Button>
        ) : (
          <Button
            type="button"
            className="h-9 flex-1"
            disabled={busy}
            onClick={() => onRun(task)}
          >
            {busy ? (
              <LoaderCircleIcon className="size-4 animate-spin" />
            ) : (
              <PlayIcon className="size-4" />
            )}
            立即执行
          </Button>
        )}
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="size-9"
          disabled={!canOpen}
          onClick={() => onOpen(task)}
          title="查看进度"
          aria-label="查看进度"
        >
          <EyeIcon className="size-4" />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="size-9"
          onClick={() => onEdit(task)}
          title="编辑"
          aria-label="编辑"
        >
          <PencilIcon className="size-4" />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="size-9 text-red-500 hover:bg-red-50 hover:text-red-600"
          onClick={() => onDelete(task)}
          title="删除"
          aria-label="删除"
        >
          <Trash2Icon className="size-4" />
        </Button>
      </div>
    </article>
  );
}
