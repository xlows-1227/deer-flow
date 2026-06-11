"use client";

import { LoaderCircle, Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";
import { useRollupThreadMemory } from "@/core/memory/hooks";
import { useThreads } from "@/core/threads/hooks";
import { pathOfThread, titleOfThread } from "@/core/threads/utils";
import { formatTimeAgo } from "@/core/utils/datetime";

export default function ChatsPage() {
  const { t } = useI18n();
  const { data: threads } = useThreads();
  const [search, setSearch] = useState("");
  const [rollupThreadId, setRollupThreadId] = useState<string | null>(null);
  const { mutateAsync: rollupThreadMemory, isPending: isRollingUpMemory } =
    useRollupThreadMemory();

  useEffect(() => {
    document.title = `${t.pages.chats} - ${t.pages.appName}`;
  }, [t.pages.chats, t.pages.appName]);

  const filteredThreads = useMemo(() => {
    return threads
      ?.filter((thread) => thread.metadata?.source !== "scheduled_task")
      .filter((thread) => {
        return titleOfThread(thread)
          .toLowerCase()
          .includes(search.toLowerCase());
      });
  }, [threads, search]);

  const handleRollupMemory = async (threadId: string) => {
    setRollupThreadId(threadId);
    try {
      const summary = await rollupThreadMemory(threadId);
      toast.success(
        summary
          ? t.conversation.memoryRollupSuccess
          : t.conversation.memoryRollupEmpty,
      );
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t.conversation.memoryRollupFailed,
      );
    } finally {
      setRollupThreadId(null);
    }
  };

  return (
    <WorkspaceContainer>
      <WorkspaceHeader showGithubLink={false}></WorkspaceHeader>
      <WorkspaceBody>
        <div className="flex size-full flex-col">
          <header className="flex shrink-0 items-center justify-center pt-8">
            <Input
              type="search"
              className="h-12 w-full max-w-(--container-width-md) text-xl"
              placeholder={t.chats.searchChats}
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </header>
          <main className="min-h-0 flex-1">
            <ScrollArea className="size-full py-4">
              <div className="mx-auto flex size-full max-w-(--container-width-md) flex-col">
                {filteredThreads?.map((thread) => (
                  <div
                    key={thread.thread_id}
                    className="flex items-center gap-3 border-b p-4"
                  >
                    <Link
                      className="min-w-0 flex-1"
                      href={pathOfThread(thread)}
                    >
                      <div className="flex flex-col gap-2">
                        <div className="truncate">{titleOfThread(thread)}</div>
                        {thread.updated_at && (
                          <div className="text-muted-foreground text-sm">
                            {formatTimeAgo(thread.updated_at)}
                          </div>
                        )}
                      </div>
                    </Link>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={isRollingUpMemory}
                      onClick={() => void handleRollupMemory(thread.thread_id)}
                    >
                      {rollupThreadId === thread.thread_id ? (
                        <LoaderCircle className="animate-spin" />
                      ) : (
                        <Sparkles />
                      )}
                      <span>{t.conversation.memoryRollup}</span>
                    </Button>
                  </div>
                ))}
              </div>
            </ScrollArea>
          </main>
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
