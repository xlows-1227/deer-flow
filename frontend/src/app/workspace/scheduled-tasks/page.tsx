"use client";

import {
  CalendarClockIcon,
  CheckCircle2Icon,
  LoaderCircleIcon,
  PlusIcon,
  RefreshCwIcon,
  XCircleIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ScheduledTaskCard } from "@/components/workspace/scheduled-tasks/task-card";
import { ScheduledTaskDialog } from "@/components/workspace/scheduled-tasks/task-dialog";
import {
  cancelScheduledTask,
  createScheduledTask,
  deleteScheduledTask,
  listScheduledTasks,
  runScheduledTask,
  toggleScheduledTask,
  updateScheduledTask,
} from "@/core/scheduled-tasks/api";
import type {
  ScheduledTask,
  ScheduledTaskPayload,
} from "@/core/scheduled-tasks/types";

function Stat({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
}) {
  return (
    <div className="flex min-h-20 items-center gap-3 rounded-lg border border-slate-200 bg-white px-4">
      <div className="flex size-10 items-center justify-center rounded-lg bg-slate-100 text-slate-600">
        {icon}
      </div>
      <div>
        <div className="text-2xl font-semibold tabular-nums text-slate-950">
          {value}
        </div>
        <div className="text-xs text-slate-500">{label}</div>
      </div>
    </div>
  );
}

export default function WorkspaceScheduledTasksPage() {
  const router = useRouter();
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<ScheduledTask | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [runningId, setRunningId] = useState<string | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const loadedOnceRef = useRef(false);

  const loadTasks = useCallback(async (silent = false) => {
    try {
      if (!silent && !loadedOnceRef.current) setLoading(true);
      if (silent) setRefreshing(true);
      const result = await listScheduledTasks();
      setTasks(result.tasks);
      loadedOnceRef.current = true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载定时任务失败");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  const hasRunning = tasks.some((task) => task.last_run_status === "running");
  useEffect(() => {
    if (!hasRunning && !runningId) return;
    const timer = window.setInterval(() => void loadTasks(true), 3000);
    return () => window.clearInterval(timer);
  }, [hasRunning, runningId, loadTasks]);

  const stats = useMemo(() => {
    const active = tasks.filter((task) => task.is_enabled).length;
    const running = tasks.filter(
      (task) => task.last_run_status === "running",
    ).length;
    const failed = tasks.filter((task) => task.last_run_status === "error").length;
    return { active, running, failed, total: tasks.length };
  }, [tasks]);

  const openCreateDialog = () => {
    setEditingTask(null);
    setFormError(null);
    setDialogOpen(true);
  };

  const openEditDialog = (task: ScheduledTask) => {
    setEditingTask(task);
    setFormError(null);
    setDialogOpen(true);
  };

  const handleSave = async (payload: ScheduledTaskPayload) => {
    setSaving(true);
    setFormError(null);
    try {
      if (editingTask) {
        await updateScheduledTask(editingTask.id, payload);
        toast.success("定时任务已更新");
      } else {
        await createScheduledTask(payload);
        toast.success("定时任务已创建");
      }
      setDialogOpen(false);
      setEditingTask(null);
      await loadTasks(true);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleRun = (task: ScheduledTask) => {
    setRunningId(task.id);
    void (async () => {
      try {
        const result = await runScheduledTask(task.id);
        toast.success("任务已开始执行");
        await loadTasks(true);
        router.push(`/workspace/chats/${result.thread_id}`);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "执行失败");
      } finally {
        setRunningId(null);
      }
    })();
  };

  const handleCancel = (task: ScheduledTask) => {
    setCancellingId(task.id);
    void (async () => {
      try {
        const result = await cancelScheduledTask(task.id);
        if (result.ok) {
          toast.success(result.message || "已取消执行");
        } else {
          toast.warning(result.message || "取消请求已发送");
        }
        await loadTasks(true);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "取消失败");
      } finally {
        setCancellingId(null);
      }
    })();
  };

  const handleToggle = (task: ScheduledTask) => {
    void (async () => {
      try {
        await toggleScheduledTask(task.id);
        await loadTasks(true);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "切换状态失败");
      }
    })();
  };

  const handleDelete = (task: ScheduledTask) => {
    if (!window.confirm(`确定删除定时任务「${task.name}」吗？`)) return;
    void (async () => {
      try {
        await deleteScheduledTask(task.id);
        toast.success("定时任务已删除");
        await loadTasks(true);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "删除失败");
      }
    })();
  };

  const handleOpen = (task: ScheduledTask) => {
    if (task.last_run_thread_id) {
      router.push(`/workspace/chats/${task.last_run_thread_id}`);
    }
  };

  return (
    <div className="flex size-full flex-col bg-slate-50">
      <header className="shrink-0 border-b border-slate-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div className="flex items-center gap-3">
            <div className="flex size-11 items-center justify-center rounded-lg bg-slate-950 text-white">
              <CalendarClockIcon className="size-5" />
            </div>
            <div>
              <h1 className="text-xl font-semibold text-slate-950">定时任务</h1>
              <p className="mt-1 text-sm text-slate-500">
                按计划自动创建 Agent 会话，适合日报、巡检、提醒和周期性分析。
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => void loadTasks(true)}
              disabled={refreshing}
            >
              <RefreshCwIcon
                className={refreshing ? "size-4 animate-spin" : "size-4"}
              />
              刷新
            </Button>
            <Button type="button" onClick={openCreateDialog}>
              <PlusIcon className="size-4" />
              新建任务
            </Button>
          </div>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
          <div className="grid gap-3 sm:grid-cols-4">
            <Stat
              icon={<CalendarClockIcon className="size-5" />}
              label="全部任务"
              value={stats.total}
            />
            <Stat
              icon={<CheckCircle2Icon className="size-5 text-emerald-600" />}
              label="已启用"
              value={stats.active}
            />
            <Stat
              icon={<LoaderCircleIcon className="size-5 text-amber-600" />}
              label="运行中"
              value={stats.running}
            />
            <Stat
              icon={<XCircleIcon className="size-5 text-red-600" />}
              label="失败任务"
              value={stats.failed}
            />
          </div>

          {loading ? (
            <div className="flex min-h-80 items-center justify-center rounded-lg border border-slate-200 bg-white">
              <div className="flex items-center gap-2 text-sm text-slate-500">
                <LoaderCircleIcon className="size-5 animate-spin" />
                正在加载定时任务
              </div>
            </div>
          ) : tasks.length === 0 ? (
            <div className="flex min-h-96 flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center">
              <CalendarClockIcon className="size-12 text-slate-300" />
              <h2 className="mt-4 text-lg font-semibold text-slate-950">
                还没有定时任务
              </h2>
              <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">
                创建一个任务，设置执行时间和提示词。到点后 DeerFlow 会自动开启会话并运行 Agent。
              </p>
              <Button type="button" className="mt-6" onClick={openCreateDialog}>
                <PlusIcon className="size-4" />
                创建第一个任务
              </Button>
            </div>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
              {tasks.map((task) => (
                <ScheduledTaskCard
                  key={task.id}
                  task={task}
                  busy={runningId === task.id}
                  cancelling={cancellingId === task.id}
                  onRun={handleRun}
                  onCancel={handleCancel}
                  onToggle={handleToggle}
                  onEdit={openEditDialog}
                  onDelete={handleDelete}
                  onOpen={handleOpen}
                />
              ))}
            </div>
          )}
        </div>
      </main>

      <ScheduledTaskDialog
        open={dialogOpen}
        task={editingTask}
        saving={saving}
        error={formError}
        onOpenChange={(open) => {
          setDialogOpen(open);
          if (!open) {
            setEditingTask(null);
            setFormError(null);
          }
        }}
        onSubmit={handleSave}
      />
    </div>
  );
}
