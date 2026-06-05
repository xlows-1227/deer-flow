"use client";

import { HistoryIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

import {
  deleteSkillAiCreateSession,
  listUnpublishedSkillAiCreateSessions,
} from "./skill-ai-create-sessions";
import { deleteLocalDraft } from "./skill-local-draft";

function formatSessionTime(timestamp: number) {
  const date = new Date(timestamp);
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();
  if (isToday) {
    return date.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function SkillAiCreateSessionHistory({
  currentThreadId,
}: {
  currentThreadId: string;
}) {
  const [open, setOpen] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [pendingDeleteThreadId, setPendingDeleteThreadId] = useState<
    string | null
  >(null);

  const sessions = useMemo(() => {
    void refreshKey;
    return listUnpublishedSkillAiCreateSessions(currentThreadId);
  }, [currentThreadId, refreshKey]);

  const handleDelete = useCallback((threadId: string) => {
    deleteSkillAiCreateSession(threadId);
    deleteLocalDraft(threadId);
    setPendingDeleteThreadId(null);
    setRefreshKey((value) => value + 1);
    toast.success("已删除会话");
  }, []);

  const handleDeleteClick = useCallback(
    (threadId: string) => {
      if (pendingDeleteThreadId === threadId) {
        handleDelete(threadId);
        return;
      }
      setPendingDeleteThreadId(threadId);
    },
    [handleDelete, pendingDeleteThreadId],
  );

  return (
    <DropdownMenu
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (!nextOpen) setPendingDeleteThreadId(null);
        if (nextOpen) setRefreshKey((value) => value + 1);
      }}
    >
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          className="size-9 shrink-0 rounded-lg text-gray-500 hover:bg-white"
          aria-label="历史创建会话"
        >
          <HistoryIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72">
        <DropdownMenuLabel>未发布的创建会话</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {sessions.length === 0 ? (
          <div className="text-muted-foreground px-2 py-3 text-xs">
            暂无其他未发布会话
          </div>
        ) : (
          sessions.map((session) => (
            <DropdownMenuItem
              key={session.threadId}
              className="cursor-default p-0 focus:bg-transparent"
              onSelect={(event) => event.preventDefault()}
            >
              <div className="flex w-full items-stretch">
                <Link
                  href={`/workspace/skills/ai-create/${session.threadId}`}
                  className={cn(
                    "flex min-w-0 flex-1 flex-col items-start gap-0.5 rounded-sm px-2 py-1.5",
                    "hover:bg-accent hover:text-accent-foreground",
                  )}
                  onClick={() => setOpen(false)}
                >
                  <span className="w-full truncate font-medium">
                    {session.title || "未命名技能"}
                  </span>
                  <span className="text-muted-foreground text-xs">
                    {session.skillName ? `${session.skillName} · ` : ""}
                    {formatSessionTime(session.updatedAt)}
                  </span>
                </Link>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className={cn(
                    "my-0.5 mr-1 size-8 shrink-0 transition-colors",
                    pendingDeleteThreadId === session.threadId
                      ? "bg-red-50 text-red-600 hover:bg-red-100 hover:text-red-700"
                      : "text-gray-400 hover:text-red-600",
                  )}
                  title={
                    pendingDeleteThreadId === session.threadId
                      ? "再次点击删除"
                      : "点击后再次点击删除"
                  }
                  aria-label={
                    pendingDeleteThreadId === session.threadId
                      ? `再次点击删除会话 ${session.title || "未命名技能"}`
                      : `删除会话 ${session.title || "未命名技能"}`
                  }
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    handleDeleteClick(session.threadId);
                  }}
                >
                  <Trash2Icon className="size-3.5" />
                </Button>
              </div>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
