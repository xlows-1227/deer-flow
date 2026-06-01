"use client";

import {
  Download,
  FileJson,
  FileText,
  LinkIcon,
  ShareIcon,
} from "lucide-react";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import { useCreateThreadShare } from "@/core/shares/hooks";
import {
  exportThreadAsJSON,
  exportThreadAsMarkdown,
} from "@/core/threads/export";
import type { AgentThread } from "@/core/threads/types";

import { useThread } from "./messages/context";
import { Tooltip } from "./tooltip";

export function ExportTrigger({ threadId }: { threadId: string }) {
  const { t } = useI18n();
  const { thread } = useThread();
  const { mutate: createShare, isPending: isSharing } = useCreateThreadShare();
  const [shareUrl, setShareUrl] = useState<string | null>(null);

  const messages = thread.messages;

  const handleExport = useCallback(
    (format: "markdown" | "json") => {
      if (messages.length === 0) {
        toast.error(t.conversation.noMessages);
        return;
      }
      const agentThread = {
        thread_id: threadId,
        updated_at: new Date().toISOString(),
        values: thread.values,
      } as AgentThread;

      if (format === "markdown") {
        exportThreadAsMarkdown(agentThread, messages);
      } else {
        exportThreadAsJSON(agentThread, messages);
      }
      toast.success(t.common.exportSuccess);
    },
    [messages, thread.values, threadId, t],
  );

  const handleShare = useCallback(() => {
    if (messages.length === 0) {
      toast.error(t.conversation.noMessages);
      return;
    }
    createShare(threadId, {
      onSuccess: (data) => {
        const url = `${window.location.origin}/share/${data.share_token}`;
        setShareUrl(url);
        toast.success("分享链接已生成");
      },
      onError: (error) => {
        toast.error(error instanceof Error ? error.message : "生成分享链接失败");
      },
    });
  }, [createShare, messages.length, threadId, t]);

  const handleCopy = useCallback(() => {
    if (!shareUrl) return;
    void navigator.clipboard.writeText(shareUrl);
    toast.success("链接已复制到剪贴板");
  }, [shareUrl]);

  if (messages.length === 0) {
    return null;
  }

  return (
    <>
      <div className="flex items-center gap-1">
        <Tooltip content="分享对话">
          <Button
            className="text-muted-foreground hover:text-foreground gap-1"
            variant="ghost"
            size="sm"
            disabled={isSharing}
            onClick={handleShare}
          >
            <ShareIcon className="h-4 w-4" />
            <span className="hidden sm:inline">分享</span>
          </Button>
        </Tooltip>
        <DropdownMenu>
          <Tooltip content={t.common.export}>
            <DropdownMenuTrigger asChild>
              <Button
                className="text-muted-foreground hover:text-foreground gap-1"
                variant="ghost"
                size="sm"
              >
                <Download className="h-4 w-4" />
                <span className="hidden sm:inline">{t.common.export}</span>
              </Button>
            </DropdownMenuTrigger>
          </Tooltip>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => handleExport("markdown")}>
              <FileText className="text-muted-foreground" />
              <span>{t.common.exportAsMarkdown}</span>
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => handleExport("json")}>
              <FileJson className="text-muted-foreground" />
              <span>{t.common.exportAsJSON}</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <Dialog open={!!shareUrl} onOpenChange={(open) => !open && setShareUrl(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>分享对话</DialogTitle>
            <DialogDescription>
              任何人都可以通过此链接查看对话记录（只读，无法继续提问）。
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-2 pt-2">
            <Input
              value={shareUrl ?? ""}
              readOnly
              className="flex-1"
              onClick={(e) => e.currentTarget.select()}
            />
            <Button onClick={handleCopy} size="sm">
              <LinkIcon className="h-4 w-4 mr-1" />
              复制
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
