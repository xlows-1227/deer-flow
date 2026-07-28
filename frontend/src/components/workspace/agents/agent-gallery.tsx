"use client";

import { BotIcon, PlusIcon, RefreshCwIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

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
import { Skeleton } from "@/components/ui/skeleton";
import { PublishedAgentCard } from "@/components/workspace/published-agents/agent-card";
import { useI18n } from "@/core/i18n/hooks";
import {
  useCreatePublishedAgent,
  usePublishedAgents,
} from "@/core/published-agents";

const SLUG_PATTERN = /^[A-Za-z0-9-]+$/;

export function AgentGallery() {
  const { t } = useI18n();
  const router = useRouter();
  const { agents, isLoading, error, refetch } = usePublishedAgents();
  const createAgent = useCreatePublishedAgent();
  const [createOpen, setCreateOpen] = useState(false);
  const [slug, setSlug] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");

  async function handleCreate() {
    const cleanSlug = slug.trim();
    const cleanName = displayName.trim();
    if (!cleanSlug || !cleanName || !SLUG_PATTERN.test(cleanSlug)) return;

    try {
      const created = await createAgent.mutateAsync({
        slug: cleanSlug,
        display_name: cleanName,
        description: description.trim() || null,
      });
      toast.success(t.publishedAgents.gallery.createSuccess);
      setCreateOpen(false);
      setSlug("");
      setDisplayName("");
      setDescription("");
      router.push(`/workspace/agents/${created.id}`);
    } catch (createError) {
      toast.error(
        createError instanceof Error ? createError.message : String(createError),
      );
    }
  }

  return (
    <>
      <div className="relative flex size-full flex-col overflow-hidden">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-52 bg-[radial-gradient(circle_at_20%_0%,color-mix(in_oklab,var(--foreground)_7%,transparent),transparent_55%)]" />

        <header className="relative border-b px-5 py-5 md:px-8">
          <div className="mx-auto flex w-full max-w-[1480px] items-end justify-between gap-6">
            <div className="min-w-0">
              <p className="text-muted-foreground font-mono text-[10px] font-semibold tracking-[0.22em]">
                {t.publishedAgents.gallery.eyebrow}
              </p>
              <h1 className="mt-2 text-2xl font-semibold tracking-tight md:text-3xl">
                {t.publishedAgents.gallery.title}
              </h1>
              <p className="text-muted-foreground mt-2 max-w-2xl text-sm leading-6">
                {t.publishedAgents.gallery.description}
              </p>
            </div>
            <Button onClick={() => setCreateOpen(true)}>
              <PlusIcon />
              {t.publishedAgents.gallery.newAgent}
            </Button>
          </div>
        </header>

        <main className="relative flex-1 overflow-y-auto px-5 py-6 md:px-8 md:py-8">
          <div className="mx-auto w-full max-w-[1480px]">
            {isLoading ? (
              <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
                {Array.from({ length: 6 }, (_, index) => (
                  <Skeleton key={index} className="h-[360px] rounded-xl" />
                ))}
              </div>
            ) : error ? (
              <div className="flex h-72 flex-col items-center justify-center gap-4 rounded-xl border border-dashed text-center">
                <div className="bg-destructive/10 text-destructive flex size-12 items-center justify-center rounded-full">
                  <RefreshCwIcon className="size-5" />
                </div>
                <div>
                  <p className="font-medium">
                    {t.publishedAgents.gallery.loadError}
                  </p>
                  <p className="text-muted-foreground mt-1 max-w-md text-sm">
                    {error instanceof Error ? error.message : String(error)}
                  </p>
                </div>
                <Button variant="outline" onClick={() => void refetch()}>
                  <RefreshCwIcon />
                  {t.publishedAgents.gallery.retry}
                </Button>
              </div>
            ) : agents.length === 0 ? (
              <div className="flex h-80 flex-col items-center justify-center gap-4 rounded-xl border border-dashed bg-muted/20 text-center">
                <div className="bg-background ring-border flex size-14 items-center justify-center rounded-2xl shadow-sm ring-1">
                  <BotIcon className="text-muted-foreground size-7" />
                </div>
                <div>
                  <p className="font-semibold">
                    {t.publishedAgents.gallery.emptyTitle}
                  </p>
                  <p className="text-muted-foreground mt-1 max-w-md text-sm leading-6">
                    {t.publishedAgents.gallery.emptyDescription}
                  </p>
                </div>
                <Button
                  variant="outline"
                  className="mt-1"
                  onClick={() => setCreateOpen(true)}
                >
                  <PlusIcon />
                  {t.publishedAgents.gallery.newAgent}
                </Button>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
                {agents.map((agent) => (
                  <PublishedAgentCard key={agent.id} agent={agent} />
                ))}
              </div>
            )}
          </div>
        </main>
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.publishedAgents.gallery.createTitle}</DialogTitle>
            <DialogDescription>
              {t.publishedAgents.gallery.createDescription}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label
                htmlFor="published-agent-slug"
                className="text-sm font-medium"
              >
                {t.publishedAgents.gallery.slug}
              </label>
              <Input
                id="published-agent-slug"
                autoFocus
                value={slug}
                placeholder="research-assistant"
                aria-invalid={Boolean(slug && !SLUG_PATTERN.test(slug))}
                onChange={(event) => setSlug(event.target.value)}
              />
              <p className="text-muted-foreground text-xs leading-5">
                {t.publishedAgents.gallery.slugHint}
              </p>
            </div>
            <div className="space-y-2">
              <label
                htmlFor="published-agent-display-name"
                className="text-sm font-medium"
              >
                {t.publishedAgents.gallery.displayName}
              </label>
              <Input
                id="published-agent-display-name"
                value={displayName}
                placeholder="Research assistant"
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <label
                htmlFor="published-agent-description"
                className="text-sm font-medium"
              >
                {t.publishedAgents.gallery.descriptionLabel}
              </label>
              <Input
                id="published-agent-description"
                value={description}
                placeholder={t.publishedAgents.gallery.descriptionPlaceholder}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setCreateOpen(false)}
              disabled={createAgent.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              onClick={() => void handleCreate()}
              disabled={
                createAgent.isPending ||
                !slug.trim() ||
                !displayName.trim() ||
                !SLUG_PATTERN.test(slug.trim())
              }
            >
              {createAgent.isPending
                ? t.publishedAgents.gallery.creating
                : t.publishedAgents.gallery.createDraft}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
