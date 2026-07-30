"use client";

import {
  ActivityIcon,
  ArchiveIcon,
  BotIcon,
  BracesIcon,
  CirclePauseIcon,
  CirclePlayIcon,
  EllipsisIcon,
  KeyRoundIcon,
  MessageSquareMoreIcon,
  RadioTowerIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useI18n } from "@/core/i18n/hooks";
import {
  useAgentChannels,
  useAgentKeys,
  useAgentReleases,
  useAgentUsage,
  usePublishedAgentLifecycle,
  type AgentUsageDay,
  type PublishedAgent,
  type PublishedAgentStatus,
} from "@/core/published-agents";
import { cn } from "@/lib/utils";

type ConfirmAction = "suspend" | "archive";

const statusStyles: Record<PublishedAgentStatus, string> = {
  draft:
    "border-slate-300 bg-slate-100 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300",
  published:
    "border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
  suspended:
    "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300",
  archived:
    "border-zinc-300 bg-zinc-100 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400",
};

function UsageSparkline({ days }: { days: AgentUsageDay[] }) {
  const points = useMemo(() => {
    if (days.length === 0) return "";
    const maximum = Math.max(...days.map((day) => day.runs), 1);
    return days
      .map((day, index) => {
        const x = days.length === 1 ? 50 : (index / (days.length - 1)) * 100;
        const y = 26 - (day.runs / maximum) * 22;
        return `${x},${y}`;
      })
      .join(" ");
  }, [days]);

  return (
    <svg
      viewBox="0 0 100 30"
      aria-hidden="true"
      className="h-8 w-full overflow-visible"
      preserveAspectRatio="none"
    >
      <path
        d="M0 27.5H100"
        className="stroke-border"
        strokeWidth="1"
        strokeDasharray="3 3"
      />
      {points ? (
        <polyline
          points={points}
          fill="none"
          className="stroke-foreground"
          strokeWidth="2.25"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : null}
    </svg>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof ActivityIcon;
  label: string;
  value: string;
}) {
  return (
    <div className="min-w-0">
      <div className="text-muted-foreground flex items-center gap-1.5 text-[11px] font-medium tracking-wide uppercase">
        <Icon className="size-3" />
        <span className="truncate">{label}</span>
      </div>
      <p className="mt-1 truncate text-sm font-semibold tabular-nums">{value}</p>
    </div>
  );
}

export function PublishedAgentCard({ agent }: { agent: PublishedAgent }) {
  const { locale, t } = useI18n();
  const router = useRouter();
  const lifecycle = usePublishedAgentLifecycle(agent.id);
  const { releases } = useAgentReleases(agent.id);
  const { keys } = useAgentKeys(agent.id);
  const { channels } = useAgentChannels(agent.id);
  const { usage } = useAgentUsage(agent.id, 7);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(
    null,
  );

  const release =
    releases.find((item) => item.id === agent.current_release_id) ??
    releases[0] ??
    null;
  const activeKeys = keys.filter(
    (key) => key.status === "active" || key.status === "overlap",
  );
  const activeChannels = channels.filter(
    (channel) => channel.status === "active",
  );
  const health =
    activeChannels.length === 0
      ? channels.length === 0
        ? "notConfigured"
        : "unknown"
      : activeChannels.some((channel) => channel.health === "unhealthy")
        ? "unhealthy"
        : activeChannels.every((channel) => channel.health === "healthy")
          ? "healthy"
          : "unknown";

  const publishedAt = release?.created_at
    ? new Intl.DateTimeFormat(locale, {
        month: "short",
        day: "numeric",
        year: "numeric",
      }).format(new Date(release.created_at))
    : null;

  async function updateStatus(action: "suspend" | "resume" | "archive") {
    try {
      await lifecycle.mutateAsync(action);
      toast.success(t.publishedAgents.gallery.statusUpdated);
      setConfirmAction(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  const statusLabel = t.publishedAgents.status[agent.status];
  const healthLabel = t.publishedAgents.health[health];

  return (
    <>
      <Card
        data-testid={`published-agent-${agent.id}`}
        className={cn(
          "group relative flex min-h-[360px] flex-col overflow-hidden border-border/80 bg-card/95 shadow-none transition-all duration-300 hover:-translate-y-0.5 hover:border-foreground/25 hover:shadow-lg",
          agent.status === "archived" && "opacity-75",
        )}
      >
        <div
          className={cn(
            "absolute inset-y-0 left-0 w-1",
            agent.status === "published" && "bg-emerald-500",
            agent.status === "draft" && "bg-slate-400",
            agent.status === "suspended" && "bg-amber-500",
            agent.status === "archived" && "bg-zinc-400",
          )}
        />

        <CardHeader className="space-y-4 pl-6">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-center gap-3">
              <div className="bg-muted ring-border flex size-11 shrink-0 items-center justify-center rounded-xl ring-1">
                <BotIcon className="text-foreground size-5" />
              </div>
              <div className="min-w-0">
                <h2 className="truncate text-base font-semibold">
                  {agent.display_name}
                </h2>
                <p className="text-muted-foreground mt-0.5 truncate font-mono text-xs">
                  /{agent.slug}
                </p>
              </div>
            </div>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={t.publishedAgents.gallery.actions}
                >
                  <EllipsisIcon />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {agent.status === "suspended" &&
                agent.current_release_id !== null ? (
                  <DropdownMenuItem
                    onSelect={() => void updateStatus("resume")}
                  >
                    <CirclePlayIcon />
                    {t.publishedAgents.gallery.resume}
                  </DropdownMenuItem>
                ) : agent.status === "published" &&
                  agent.current_release_id !== null ? (
                  <DropdownMenuItem
                    onSelect={() => setConfirmAction("suspend")}
                  >
                    <CirclePauseIcon />
                    {t.publishedAgents.gallery.suspend}
                  </DropdownMenuItem>
                ) : null}
                {agent.status !== "archived" ? (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      className="text-destructive focus:text-destructive"
                      onSelect={() => setConfirmAction("archive")}
                    >
                      <ArchiveIcon />
                      {t.publishedAgents.gallery.archive}
                    </DropdownMenuItem>
                  </>
                ) : null}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant="outline"
              className={cn("border", statusStyles[agent.status])}
            >
              <span className="size-1.5 rounded-full bg-current" />
              {statusLabel}
            </Badge>
            <Badge variant="outline" className="font-mono font-normal">
              {release
                ? t.publishedAgents.gallery.release(release.release_no)
                : t.publishedAgents.gallery.noRelease}
            </Badge>
          </div>

          <p className="text-muted-foreground line-clamp-2 min-h-10 text-sm leading-5">
            {agent.description ?? "—"}
          </p>
        </CardHeader>

        <CardContent className="flex flex-1 flex-col gap-4 pl-6">
          <div className="grid grid-cols-2 gap-x-4 gap-y-4 border-y py-4">
            <Metric
              icon={KeyRoundIcon}
              label={t.publishedAgents.gallery.integrations}
              value={t.publishedAgents.gallery.apiKeyCount(activeKeys.length)}
            />
            <Metric
              icon={MessageSquareMoreIcon}
              label="Feishu"
              value={t.publishedAgents.gallery.feishuCount(
                activeChannels.length,
              )}
            />
            <Metric
              icon={ActivityIcon}
              label={t.publishedAgents.gallery.health}
              value={healthLabel}
            />
            <Metric
              icon={RadioTowerIcon}
              label={t.publishedAgents.gallery.publishedAt}
              value={publishedAt ?? "—"}
            />
          </div>

          <div className="mt-auto">
            <div className="mb-1 flex items-end justify-between gap-3">
              <span className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
                {t.publishedAgents.gallery.usage7d}
              </span>
              <span className="text-xs font-semibold tabular-nums">
                {t.publishedAgents.gallery.runsCount(
                  usage?.totals.runs ?? 0,
                )}
              </span>
            </div>
            <UsageSparkline days={usage?.days ?? []} />
          </div>
        </CardContent>

        <CardFooter className="gap-2 pl-6">
          <Button
            className="flex-1"
            onClick={() => router.push(`/workspace/agents/${agent.id}`)}
          >
            <BracesIcon />
            {t.publishedAgents.gallery.studio}
          </Button>
        </CardFooter>
      </Card>

      <Dialog
        open={confirmAction !== null}
        onOpenChange={(open) => !open && setConfirmAction(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {confirmAction === "archive"
                ? t.publishedAgents.gallery.confirmArchiveTitle
                : t.publishedAgents.gallery.confirmSuspendTitle}
            </DialogTitle>
            <DialogDescription>
              {confirmAction === "archive"
                ? t.publishedAgents.gallery.confirmArchiveDescription
                : t.publishedAgents.gallery.confirmSuspendDescription}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmAction(null)}
              disabled={lifecycle.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant={confirmAction === "archive" ? "destructive" : "default"}
              disabled={lifecycle.isPending || confirmAction === null}
              onClick={() =>
                confirmAction && void updateStatus(confirmAction)
              }
            >
              {confirmAction === "archive"
                ? t.publishedAgents.gallery.archive
                : t.publishedAgents.gallery.suspend}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
