"use client";

import type { AIMessage, Message } from "@langchain/langgraph-sdk";
import {
  CheckCircle2Icon,
  CircleIcon,
  LightbulbIcon,
  ListTodoIcon,
  LoaderCircleIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

import { useI18n } from "@/core/i18n/hooks";
import {
  extractReasoningContentFromMessage,
  findToolCallResult,
} from "@/core/messages/utils";
import type { Todo } from "@/core/todos";
import { explainToolCall } from "@/core/tools/utils";
import { cn } from "@/lib/utils";

import { useThread } from "../messages/context";
import { StreamingIndicator } from "../streaming-indicator";

const STATUS_LABELS: Record<NonNullable<Todo["status"]>, string> = {
  pending: "待执行",
  in_progress: "当前",
  completed: "已完成",
};

function StepStatusIcon({ status }: { status: Todo["status"] }) {
  if (status === "completed") {
    return <CheckCircle2Icon className="size-4 text-emerald-600" />;
  }
  if (status === "in_progress") {
    return <LoaderCircleIcon className="size-4 animate-spin text-blue-600" />;
  }
  return <CircleIcon className="size-4 text-slate-300" />;
}

function TodoProgress({ todos }: { todos: Todo[] }) {
  const completed = todos.filter((todo) => todo.status === "completed").length;
  const currentIndex = todos.findIndex((todo) => todo.status === "in_progress");

  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold tracking-wider text-slate-600 uppercase">
          <ListTodoIcon className="size-3.5 text-slate-400" />
          任务步骤
        </div>
        {todos.length > 0 && (
          <span className="text-xs text-slate-500">
            {completed}/{todos.length}
          </span>
        )}
      </div>
      {todos.length === 0 ? (
        <p className="text-sm text-slate-400">
          Pro/Ultra 模式开始规划后，这里会显示当前步骤。
        </p>
      ) : (
        <div className="space-y-1.5">
          {todos.map((todo, index) => {
            const status = todo.status ?? "pending";
            const isCurrent =
              status === "in_progress" ||
              (currentIndex === -1 &&
                status === "pending" &&
                index === completed);
            return (
              <div
                key={`${todo.content ?? "step"}-${index}`}
                className={cn(
                  "flex gap-2 rounded-md border px-2.5 py-2 text-sm",
                  isCurrent
                    ? "border-blue-200 bg-blue-50"
                    : "border-slate-200 bg-white",
                )}
              >
                <StepStatusIcon status={isCurrent ? "in_progress" : status} />
                <div className="min-w-0 flex-1">
                  <div className="line-clamp-2 text-slate-800">
                    {todo.content ?? `步骤 ${index + 1}`}
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {isCurrent ? "当前" : STATUS_LABELS[status]}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function getLastAssistantMessage(messages: Message[]): AIMessage | null {
  const assistants = messages.filter(
    (message): message is AIMessage => message.type === "ai",
  );
  if (assistants.length === 0) return null;
  return assistants[assistants.length - 1] ?? null;
}

export function WorkspaceLiveProgressPanel() {
  const { t } = useI18n();
  const { thread } = useThread();
  const scrollRef = useRef<HTMLDivElement>(null);
  const isStreaming = thread.isLoading;
  const todos = useMemo(
    () => thread.values.todos ?? [],
    [thread.values.todos],
  );
  const lastAssistant = useMemo(
    () => getLastAssistantMessage(thread.messages),
    [thread.messages],
  );

  const reasoning = lastAssistant
    ? extractReasoningContentFromMessage(lastAssistant)
    : null;

  const pendingToolCall = useMemo(() => {
    if (!lastAssistant?.tool_calls?.length) return null;
    for (let i = lastAssistant.tool_calls.length - 1; i >= 0; i -= 1) {
      const toolCall = lastAssistant.tool_calls[i]!;
      if (toolCall.name === "task" || !toolCall.id) continue;
      const result = findToolCallResult(toolCall.id, thread.messages);
      if (!result) return toolCall;
    }
    return null;
  }, [lastAssistant, thread.messages]);

  useEffect(() => {
    if (!isStreaming || !scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [isStreaming, reasoning, pendingToolCall, todos]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-slate-100 bg-slate-50/50 px-4 py-2.5">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold tracking-wider text-slate-600 uppercase">
            {isStreaming ? "执行中…" : "执行过程"}
          </h3>
          {isStreaming && (
            <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-600">
              实时
            </span>
          )}
        </div>
        <p className="mt-1 text-[11px] text-slate-400">
          {isStreaming
            ? "正在实时显示 Agent 的思考与工具执行。"
            : "展示最近一次助手回复的思考与工具调用。"}
        </p>
      </div>
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-3"
      >
        <TodoProgress todos={todos} />
        {isStreaming && !reasoning && !pendingToolCall && (
          <div className="flex items-center gap-2 text-sm text-indigo-600">
            <StreamingIndicator size="sm" />
            <span>Agent 正在思考中…</span>
          </div>
        )}
        {reasoning && (
          <section className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-600">
              <LightbulbIcon className="size-3.5 text-amber-500" />
              思考过程
            </div>
            <p className="text-sm leading-relaxed whitespace-pre-wrap text-slate-700">
              {reasoning}
            </p>
          </section>
        )}
        {pendingToolCall && (
          <section className="rounded-lg border border-blue-200 bg-blue-50/60 p-3">
            <div className="mb-1 flex items-center gap-2 text-xs font-semibold text-blue-700">
              <LoaderCircleIcon className="size-3.5 animate-spin" />
              当前工具
            </div>
            <p className="text-sm text-blue-900">
              {explainToolCall(pendingToolCall, t)}
            </p>
          </section>
        )}
        {!isStreaming && !reasoning && !pendingToolCall && todos.length === 0 && (
          <p className="text-sm text-slate-400">
            暂无可展示的执行过程，请先触发一次包含工具调用的对话。
          </p>
        )}
      </div>
    </div>
  );
}
