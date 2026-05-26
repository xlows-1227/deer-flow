"use client";

import {
  CalendarClockIcon,
  CheckIcon,
  ClockIcon,
  LoaderCircleIcon,
  SparklesIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useModels } from "@/core/models/hooks";
import type {
  ScheduledTask,
  ScheduledTaskMode,
  ScheduledTaskPayload,
  ScheduledTaskReasoningEffort,
  ScheduledTaskRepeatType,
} from "@/core/scheduled-tasks/types";
import { cn } from "@/lib/utils";

const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

const repeatOptions: Array<{
  value: ScheduledTaskRepeatType;
  label: string;
  description: string;
}> = [
  { value: "once", label: "一次", description: "到点执行一次" },
  { value: "daily", label: "每天", description: "每天固定时间" },
  { value: "weekly", label: "每周", description: "每周指定日期" },
];

const modes: Array<{
  value: ScheduledTaskMode;
  label: string;
  description: string;
}> = [
  { value: "flash", label: "Flash", description: "轻量快速" },
  { value: "thinking", label: "Thinking", description: "推理增强" },
  { value: "pro", label: "Pro", description: "计划模式" },
  { value: "ultra", label: "Ultra", description: "可用子任务" },
];

const effortOptions: Array<{
  value: ScheduledTaskReasoningEffort;
  label: string;
}> = [
  { value: "minimal", label: "Minimal" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

function defaultEffort(mode: ScheduledTaskMode): ScheduledTaskReasoningEffort | null {
  if (mode === "ultra") return "high";
  if (mode === "pro") return "medium";
  if (mode === "thinking") return "low";
  return null;
}

function taskToInitial(task: ScheduledTask | null): ScheduledTaskPayload {
  return {
    name: task?.name ?? "",
    prompt: task?.prompt ?? "",
    repeat_type: task?.repeat_type ?? "daily",
    execution_time: task?.execution_time ?? "09:00",
    day_of_week: task?.day_of_week ?? 0,
    is_enabled: task?.is_enabled ?? true,
    model_name: task?.model_name ?? null,
    mode: task?.mode ?? "pro",
    reasoning_effort: task?.reasoning_effort ?? "medium",
  };
}

export function ScheduledTaskDialog({
  open,
  task,
  saving,
  error,
  onOpenChange,
  onSubmit,
}: {
  open: boolean;
  task: ScheduledTask | null;
  saving: boolean;
  error: string | null;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: ScheduledTaskPayload) => void;
}) {
  const [form, setForm] = useState<ScheduledTaskPayload>(() =>
    taskToInitial(task),
  );
  const { models } = useModels({ enabled: open });

  useEffect(() => {
    if (open) {
      setForm(taskToInitial(task));
    }
  }, [open, task]);

  const missingRequired = !form.name.trim() || !form.prompt.trim();
  const selectedModelLabel = useMemo(() => {
    if (!form.model_name) return "使用默认模型";
    return (
      models.find((model) => model.name === form.model_name)?.display_name ??
      form.model_name
    );
  }, [form.model_name, models]);

  const update = <K extends keyof ScheduledTaskPayload>(
    key: K,
    value: ScheduledTaskPayload[K],
  ) => setForm((prev) => ({ ...prev, [key]: value }));

  const handleMode = (mode: ScheduledTaskMode) => {
    setForm((prev) => ({
      ...prev,
      mode,
      reasoning_effort: defaultEffort(mode),
    }));
  };

  const submit = () => {
    if (missingRequired) return;
    onSubmit({
      ...form,
      name: form.name.trim(),
      prompt: form.prompt.trim(),
      day_of_week: form.repeat_type === "weekly" ? form.day_of_week : null,
      reasoning_effort:
        form.mode === "flash" ? null : form.reasoning_effort ?? defaultEffort(form.mode),
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-hidden p-0 sm:max-w-2xl">
        <DialogHeader className="border-b border-slate-200 px-6 py-5">
          <DialogTitle className="flex items-center gap-2">
            <CalendarClockIcon className="size-5 text-slate-600" />
            {task ? "编辑定时任务" : "新建定时任务"}
          </DialogTitle>
          <DialogDescription>
            设置触发时间和 Agent 运行方式，任务到点后会创建独立会话。
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[calc(92vh-150px)] overflow-y-auto px-6 py-5">
          <div className="grid gap-5">
            {error && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {error}
              </div>
            )}

            <label className="grid gap-1.5">
              <span className="text-sm font-medium text-slate-800">任务名称</span>
              <Input
                value={form.name}
                maxLength={120}
                onChange={(event) => update("name", event.target.value)}
                placeholder="例如：每日数据巡检"
              />
            </label>

            <div className="grid gap-3 sm:grid-cols-[1fr_160px]">
              <div className="grid gap-1.5">
                <span className="text-sm font-medium text-slate-800">重复频率</span>
                <div className="grid grid-cols-3 gap-2">
                  {repeatOptions.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() =>
                        setForm((prev) => ({
                          ...prev,
                          repeat_type: option.value,
                          day_of_week:
                            option.value === "weekly" ? prev.day_of_week ?? 0 : null,
                        }))
                      }
                      className={cn(
                        "min-h-16 rounded-lg border px-3 py-2 text-left transition",
                        form.repeat_type === option.value
                          ? "border-slate-900 bg-slate-950 text-white"
                          : "border-slate-200 bg-white text-slate-700 hover:border-slate-300",
                      )}
                    >
                      <span className="block text-sm font-semibold">
                        {option.label}
                      </span>
                      <span
                        className={cn(
                          "mt-1 block text-xs",
                          form.repeat_type === option.value
                            ? "text-slate-300"
                            : "text-slate-500",
                        )}
                      >
                        {option.description}
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              <label className="grid gap-1.5">
                <span className="text-sm font-medium text-slate-800">执行时间</span>
                <div className="relative">
                  <ClockIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-slate-400" />
                  <Input
                    type="time"
                    value={form.execution_time}
                    onChange={(event) =>
                      update("execution_time", event.target.value)
                    }
                    className="pl-9"
                  />
                </div>
              </label>
            </div>

            {form.repeat_type === "weekly" && (
              <div className="grid gap-1.5">
                <span className="text-sm font-medium text-slate-800">每周几执行</span>
                <div className="grid grid-cols-7 gap-2">
                  {weekdays.map((label, index) => (
                    <button
                      key={label}
                      type="button"
                      onClick={() => update("day_of_week", index)}
                      className={cn(
                        "h-10 rounded-lg border text-sm font-medium transition",
                        form.day_of_week === index
                          ? "border-slate-900 bg-slate-950 text-white"
                          : "border-slate-200 bg-white text-slate-600 hover:border-slate-300",
                      )}
                    >
                      {label.replace("周", "")}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <label className="grid gap-1.5">
              <span className="text-sm font-medium text-slate-800">执行提示词</span>
              <Textarea
                value={form.prompt}
                rows={7}
                onChange={(event) => update("prompt", event.target.value)}
                placeholder="描述 Agent 到点后要完成的任务、输入来源和输出格式。"
                className="resize-y"
              />
            </label>

            <div className="grid gap-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                <SparklesIcon className="size-4 text-slate-500" />
                运行设置
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-1.5">
                  <span className="text-sm font-medium text-slate-800">模型</span>
                  <Select
                    value={form.model_name ?? "__default__"}
                    onValueChange={(value) =>
                      update("model_name", value === "__default__" ? null : value)
                    }
                  >
                    <SelectTrigger className="w-full bg-white">
                      <SelectValue>{selectedModelLabel}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__default__">使用默认模型</SelectItem>
                      {models.map((model) => (
                        <SelectItem key={model.name} value={model.name}>
                          {model.display_name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="grid gap-1.5">
                  <span className="text-sm font-medium text-slate-800">推理强度</span>
                  <Select
                    value={form.reasoning_effort ?? "low"}
                    disabled={form.mode === "flash"}
                    onValueChange={(value) =>
                      update(
                        "reasoning_effort",
                        value as ScheduledTaskReasoningEffort,
                      )
                    }
                  >
                    <SelectTrigger className="w-full bg-white">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {effortOptions.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                {modes.map((mode) => (
                  <button
                    key={mode.value}
                    type="button"
                    onClick={() => handleMode(mode.value)}
                    className={cn(
                      "min-h-16 rounded-lg border px-3 py-2 text-left transition",
                      form.mode === mode.value
                        ? "border-slate-900 bg-white shadow-sm"
                        : "border-slate-200 bg-white/70 text-slate-600 hover:border-slate-300",
                    )}
                  >
                    <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-900">
                      {form.mode === mode.value && <CheckIcon className="size-3.5" />}
                      {mode.label}
                    </span>
                    <span className="mt-1 block text-xs text-slate-500">
                      {mode.description}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-4 py-3">
              <div>
                <div className="text-sm font-medium text-slate-900">立即启用</div>
                <div className="mt-0.5 text-xs text-slate-500">
                  关闭后任务会保留，但不会被后台调度触发。
                </div>
              </div>
              <Switch
                checked={form.is_enabled}
                onCheckedChange={(checked) => update("is_enabled", checked)}
              />
            </div>
          </div>
        </div>

        <DialogFooter className="border-t border-slate-200 px-6 py-4">
          <Button
            type="button"
            variant="outline"
            disabled={saving}
            onClick={() => onOpenChange(false)}
          >
            取消
          </Button>
          <Button type="button" disabled={saving || missingRequired} onClick={submit}>
            {saving && <LoaderCircleIcon className="size-4 animate-spin" />}
            {task ? "保存修改" : "创建任务"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
