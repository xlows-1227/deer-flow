"use client";

import { useParams } from "next/navigation";

import { useSharedThread } from "@/core/shares/hooks";

export default function SharedThreadPage() {
  const { token } = useParams<{ token: string }>();
  const { thread, isLoading, error } = useSharedThread(token);

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center text-gray-500">
        加载对话中...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center text-red-600">
        {error instanceof Error ? error.message : "加载失败"}
      </div>
    );
  }

  if (!thread) {
    return (
      <div className="flex h-screen items-center justify-center text-gray-500">
        对话不存在或已过期
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col bg-white">
      <header className="sticky top-0 z-30 border-b border-gray-200 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
          <div>
            <h1 className="text-base font-semibold text-gray-900">
              {thread.title ?? "未命名对话"}
            </h1>
            <p className="text-xs text-gray-500">
              分享链接 · 只读模式
            </p>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-6">
        <div className="flex flex-col gap-6">
          {thread.messages.map((msg, idx) => (
            <div key={msg.id ?? idx} className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span
                  className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium text-white ${
                    msg.type === "human"
                      ? "bg-blue-500"
                      : msg.type === "ai"
                        ? "bg-emerald-500"
                        : "bg-gray-400"
                  }`}
                >
                  {msg.type === "human" ? "U" : msg.type === "ai" ? "A" : "T"}
                </span>
                <span className="text-xs font-medium text-gray-500">
                  {msg.type === "human"
                    ? "用户"
                    : msg.type === "ai"
                      ? "助手"
                      : "工具"}
                </span>
              </div>
              <div className="ml-8 rounded-lg bg-gray-50 p-4 text-sm leading-relaxed text-gray-800 whitespace-pre-wrap">
                {msg.content}
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
