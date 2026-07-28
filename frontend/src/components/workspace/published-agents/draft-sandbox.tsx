"use client";

import {
  ArrowUpRightIcon,
  FlaskConicalIcon,
  LoaderCircleIcon,
  ShieldIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";
import { useDraftSandboxRun } from "@/core/published-agents";

export function DraftSandbox({
  agentId,
  agentSlug,
}: {
  agentId: string;
  agentSlug: string;
}) {
  const { t } = useI18n();
  const router = useRouter();
  const sandboxRun = useDraftSandboxRun(agentId);
  const [message, setMessage] = useState("");

  async function runDraft() {
    try {
      await sandboxRun.mutateAsync(message);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <div className="relative overflow-hidden rounded-2xl border bg-card p-6 md:p-8">
      <div className="pointer-events-none absolute top-0 right-0 size-64 translate-x-1/3 -translate-y-1/3 rounded-full bg-amber-400/10 blur-3xl" />
      <div className="relative max-w-2xl">
        <div className="mb-5 flex size-12 items-center justify-center rounded-2xl bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300">
          <FlaskConicalIcon className="size-5" />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-xl font-semibold">
            {t.publishedAgents.studio.sandboxTitle}
          </h2>
          <Badge className="border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
            {t.publishedAgents.studio.notLive}
          </Badge>
        </div>
        <p className="text-muted-foreground mt-2 text-sm leading-6">
          {t.publishedAgents.studio.sandboxDescription}
        </p>

        <div className="mt-6 flex items-start gap-3 rounded-xl border bg-background/80 p-4">
          <ShieldIcon className="mt-0.5 size-4 shrink-0" />
          <p className="text-muted-foreground text-sm leading-6">
            {t.publishedAgents.studio.sandboxSafety}
          </p>
        </div>

        <div className="mt-6 space-y-2">
          <label
            className="text-sm font-medium"
            htmlFor={`draft-sandbox-message-${agentId}`}
          >
            {t.publishedAgents.studio.sandboxMessageLabel}
          </label>
          <Textarea
            id={`draft-sandbox-message-${agentId}`}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder={t.publishedAgents.studio.sandboxMessagePlaceholder}
            rows={4}
          />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button
            disabled={!message.trim() || sandboxRun.isPending}
            onClick={() => void runDraft()}
          >
            {sandboxRun.isPending ? (
              <LoaderCircleIcon className="animate-spin" />
            ) : (
              <FlaskConicalIcon />
            )}
            {sandboxRun.isPending
              ? t.publishedAgents.studio.runningSandbox
              : t.publishedAgents.studio.runSandbox}
          </Button>
          {sandboxRun.data ? (
            <>
              <Badge variant="outline">
                {t.publishedAgents.studio.sandboxStarted(
                  sandboxRun.data.draft_revision,
                )}
              </Badge>
              <Button
                variant="outline"
                onClick={() =>
                  router.push(
                    `/workspace/agents/${agentSlug}/chats/${sandboxRun.data.thread_id}`,
                  )
                }
              >
                {t.publishedAgents.studio.openSandbox}
                <ArrowUpRightIcon />
              </Button>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
