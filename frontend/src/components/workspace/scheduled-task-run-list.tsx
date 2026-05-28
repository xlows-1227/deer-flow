"use client";

import { useQuery } from "@tanstack/react-query";
import {
  BanIcon,
  CheckCircle2Icon,
  LoaderCircleIcon,
  StopCircleIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
} from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";
import { getTaskHistory, listScheduledTasks } from "@/core/scheduled-tasks/api";
import type {
  ScheduledTask,
  ScheduledTaskRunHistory,
} from "@/core/scheduled-tasks/types";
import { cn } from "@/lib/utils";

type TaskRunRecord = ScheduledTaskRunHistory & {
  task_id: string;
  task_name: string;
};

const MAX_TASKS_TO_FETCH = 10;
const MAX_RECORDS_TO_SHOW = 8;

function fallbackRunFromTask(task: ScheduledTask): TaskRunRecord | null {
  if (!task.last_run_id || !task.last_run_status) return null;
  return {
    run_id: task.last_run_id,
    thread_id: task.last_run_thread_id,
    status: task.last_run_status,
    created_at: task.last_run_at ?? task.updated_at,
    updated_at: task.last_run_at ?? task.updated_at,
    error: null,
    task_id: task.id,
    task_name: task.name,
  };
}

function runTimestamp(run: Pick<TaskRunRecord, "created_at" | "updated_at">) {
  const createdAt = run.created_at.trim();
  return createdAt.length > 0 ? createdAt : run.updated_at;
}

async function fetchTaskRunRecords(): Promise<TaskRunRecord[]> {
  const { tasks } = await listScheduledTasks();
  const candidates = tasks
    .filter(
      (task) => Boolean(task.last_run_id) || task.last_run_status === "running",
    )
    .sort((a, b) =>
      (b.last_run_at ?? b.updated_at).localeCompare(
        a.last_run_at ?? a.updated_at,
      ),
    )
    .slice(0, MAX_TASKS_TO_FETCH);

  const histories = await Promise.all(
    candidates.map(async (task) => {
      try {
        const history = await getTaskHistory(task.id);
        return history.runs.map((run) => ({
          ...run,
          task_id: task.id,
          task_name: task.name,
        }));
      } catch {
        const fallback = fallbackRunFromTask(task);
        return fallback ? [fallback] : [];
      }
    }),
  );

  return histories
    .flat()
    .sort((a, b) => runTimestamp(b).localeCompare(runTimestamp(a)))
    .slice(0, MAX_RECORDS_TO_SHOW);
}

function formatDateTime(value: string) {
  if (!value) return "";
  return new Date(value).toLocaleString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function StatusIcon({ status }: { status: TaskRunRecord["status"] }) {
  if (status === "running") {
    return <LoaderCircleIcon className="size-3.5 animate-spin" />;
  }
  if (status === "success") {
    return <CheckCircle2Icon className="size-3.5" />;
  }
  if (status === "error") {
    return <BanIcon className="size-3.5" />;
  }
  return <StopCircleIcon className="size-3.5" />;
}

function statusClassName(status: TaskRunRecord["status"]) {
  if (status === "running") return "text-amber-600";
  if (status === "success") return "text-emerald-600";
  if (status === "error") return "text-red-600";
  return "text-slate-500";
}

export function ScheduledTaskRunList() {
  const { t } = useI18n();
  const { data, isError, isLoading } = useQuery({
    queryKey: ["scheduled-tasks", "sidebar-runs"],
    queryFn: fetchTaskRunRecords,
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
    retry: false,
  });

  const records = useMemo(() => data ?? [], [data]);

  return (
    <SidebarGroup className="border-t border-gray-100 px-2 pt-1 pb-2">
      <SidebarGroupLabel className="h-6">
        {t.sidebar.taskRecords}
      </SidebarGroupLabel>
      <SidebarGroupContent className="px-2 pb-1">
        {isLoading ? (
          <div className="text-muted-foreground flex h-10 items-center gap-2 rounded-md px-2 text-xs">
            <LoaderCircleIcon className="size-3.5 animate-spin" />
            {t.common.loading}
          </div>
        ) : isError ? (
          <div className="text-muted-foreground rounded-md px-2 py-2 text-xs leading-5">
            {t.sidebar.taskRecordsLoadFailed}
          </div>
        ) : records.length === 0 ? (
          <div className="text-muted-foreground rounded-md px-2 py-2 text-xs leading-5">
            {t.sidebar.taskRecordsEmpty}
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {records.map((record) => {
              const content = (
                <>
                  <span
                    className={cn(
                      "mt-0.5 shrink-0",
                      statusClassName(record.status),
                    )}
                  >
                    <StatusIcon status={record.status} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-gray-800">
                      {record.task_name}
                    </span>
                    <span className="text-muted-foreground mt-0.5 flex items-center gap-1.5 text-[11px]">
                      <span>{t.sidebar.taskRunStatus[record.status]}</span>
                      <span aria-hidden="true">{"\u00b7"}</span>
                      <span>{formatDateTime(runTimestamp(record))}</span>
                    </span>
                  </span>
                </>
              );

              if (record.thread_id) {
                return (
                  <Link
                    key={`${record.task_id}-${record.run_id}`}
                    href={`/workspace/chats/${record.thread_id}`}
                    className="flex min-h-10 items-start gap-2 rounded-md px-2 py-2 text-left hover:bg-gray-100"
                    title={t.sidebar.taskRecordsOpen}
                  >
                    {content}
                  </Link>
                );
              }

              return (
                <div
                  key={`${record.task_id}-${record.run_id}`}
                  className="flex min-h-10 items-start gap-2 rounded-md px-2 py-2 text-left"
                >
                  {content}
                </div>
              );
            })}
          </div>
        )}
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
