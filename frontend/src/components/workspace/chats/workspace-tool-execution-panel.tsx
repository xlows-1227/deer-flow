"use client";

import type { Message } from "@langchain/langgraph-sdk";
import {
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  LoaderCircleIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { useI18n } from "@/core/i18n/hooks";
import { findToolCallResult } from "@/core/messages/utils";
import { explainToolCall } from "@/core/tools/utils";
import { cn } from "@/lib/utils";

import { useThread } from "../messages/context";

type ToolExecution = {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result: string | null;
  isExecuting: boolean;
};

function collectToolExecutions(
  messages: Message[],
  isStreaming: boolean,
): ToolExecution[] {
  const executions: ToolExecution[] = [];
  let lastPendingId: string | null = null;

  for (const message of messages) {
    if (message.type !== "ai" || !message.tool_calls?.length) continue;
    for (const toolCall of message.tool_calls) {
      if (toolCall.name === "task" || !toolCall.id) continue;
      const result = findToolCallResult(toolCall.id, messages) ?? null;
      executions.push({
        id: toolCall.id,
        name: toolCall.name,
        args: (toolCall.args ?? {}) as Record<string, unknown>,
        result,
        isExecuting: false,
      });
      if (!result) {
        lastPendingId = toolCall.id;
      }
    }
  }

  if (isStreaming && lastPendingId) {
    const index = executions.findIndex((item) => item.id === lastPendingId);
    if (index >= 0) {
      executions[index] = { ...executions[index]!, isExecuting: true };
    }
  }

  return executions;
}

function formatJson(value: unknown, maxLength = 20_000): string {
  try {
    const text =
      typeof value === "string" ? value : JSON.stringify(value, null, 2);
    if (text.length <= maxLength) return text;
    return `${text.slice(0, maxLength)}...`;
  } catch {
    return String(value);
  }
}

function ToolExecutionItem({ execution }: { execution: ToolExecution }) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const label = explainToolCall(
    {
      name: execution.name,
      args: execution.args,
      id: execution.id,
      type: "tool_call",
    },
    t,
  );

  const status = execution.isExecuting
    ? "executing"
    : execution.result
      ? "success"
      : "pending";

  const statusIcon = {
    executing: (
      <LoaderCircleIcon className="size-4 shrink-0 animate-spin text-blue-500" />
    ),
    success: <CheckCircle2Icon className="size-4 shrink-0 text-emerald-500" />,
    pending: <WrenchIcon className="size-4 shrink-0 text-slate-400" />,
    error: <XCircleIcon className="size-4 shrink-0 text-red-500" />,
  }[status];

  const statusBadge = {
    executing: (
      <Badge variant="secondary" className="bg-blue-50 text-blue-700">
        执行中
      </Badge>
    ),
    success: (
      <Badge variant="secondary" className="bg-emerald-50 text-emerald-700">
        完成
      </Badge>
    ),
    pending: <Badge variant="secondary">等待</Badge>,
    error: <Badge variant="destructive">失败</Badge>,
  }[status];

  return (
    <div className="rounded-lg border border-amber-200/80 bg-amber-50/80">
      <button
        type="button"
        onClick={() => setExpanded((open) => !open)}
        className="flex w-full items-center gap-2 rounded-t-lg px-3 py-2 text-left transition-colors hover:bg-amber-100/80"
      >
        {expanded ? (
          <ChevronDownIcon className="size-4 shrink-0 text-slate-500" />
        ) : (
          <ChevronRightIcon className="size-4 shrink-0 text-slate-500" />
        )}
        {statusIcon}
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-900">
          {label}
        </span>
        <span className="shrink-0 font-mono text-[10px] text-slate-400">
          {execution.name}
        </span>
        {statusBadge}
      </button>
      {expanded && (
        <div className="space-y-3 border-t border-amber-200/80 px-3 py-2">
          <div>
            <div className="mb-1 text-xs font-medium text-slate-500">参数</div>
            <pre className="overflow-x-auto rounded border border-slate-200 bg-white p-2 text-xs text-slate-800">
              <code>{formatJson(execution.args)}</code>
            </pre>
          </div>
          {execution.result && (
            <div>
              <div className="mb-1 text-xs font-medium text-slate-500">
                结果
              </div>
              <pre className="overflow-x-auto rounded border border-emerald-200 bg-emerald-50/60 p-2 text-xs whitespace-pre-wrap text-emerald-900">
                <code>{formatJson(execution.result)}</code>
              </pre>
            </div>
          )}
          {execution.isExecuting && (
            <div className="flex items-center gap-2 text-sm text-blue-600">
              <LoaderCircleIcon className="size-4 animate-spin" />
              <span>正在执行…</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function WorkspaceToolExecutionPanel({
  className,
}: {
  className?: string;
}) {
  const { thread } = useThread();
  const executions = useMemo(
    () => collectToolExecutions(thread.messages, thread.isLoading),
    [thread.isLoading, thread.messages],
  );

  if (executions.length === 0) {
    return (
      <div
        className={cn(
          "flex flex-1 items-center justify-center p-8 text-sm text-slate-500",
          className,
        )}
      >
        当前会话暂无工具执行记录
      </div>
    );
  }

  return (
    <div
      className={cn("min-h-0 flex-1 space-y-2 overflow-y-auto p-3", className)}
    >
      {executions.map((execution) => (
        <ToolExecutionItem key={execution.id} execution={execution} />
      ))}
    </div>
  );
}
