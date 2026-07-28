"use client";

import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  GitCompareArrowsIcon,
  HistoryIcon,
  RotateCcwIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { useI18n } from "@/core/i18n/hooks";
import {
  useRollbackAgent,
  type AgentDraft,
  type AgentRelease,
} from "@/core/published-agents";

interface SetDiff {
  added: string[];
  removed: string[];
}

export interface ReleaseDiff {
  instructionsChanged: boolean;
  agentMarkdown: { before: string; after: string };
  soulMarkdown: { before: string; after: string };
  modelChanged: boolean;
  model: { before: string | null; after: string | null };
  toolGroups: SetDiff;
  skills: SetDiff;
  connectorGrants: SetDiff;
}

function setDiff(before: string[], after: string[]): SetDiff {
  const beforeSet = new Set(before);
  const afterSet = new Set(after);
  return {
    added: [...afterSet].filter((item) => !beforeSet.has(item)).sort(),
    removed: [...beforeSet].filter((item) => !afterSet.has(item)).sort(),
  };
}

function releaseSkillNames(release: AgentRelease): string[] {
  return release.skills.map(
    (skill) => skill.skill_name ?? skill.skill_revision_id,
  );
}

function grantNames(
  grants: { connector_instance_id: string; capability: string }[],
): string[] {
  return grants.map(
    (grant) => `${grant.connector_instance_id} · ${grant.capability}`,
  );
}

function createDiff(
  before: {
    agent_markdown: string;
    soul_markdown: string;
    model_name: string | null;
    tool_groups: string[];
    skills: string[];
    connector_grants: string[];
  },
  after: {
    agent_markdown: string;
    soul_markdown: string;
    model_name: string | null;
    tool_groups: string[];
    skills: string[];
    connector_grants: string[];
  },
): ReleaseDiff {
  return {
    instructionsChanged:
      before.agent_markdown !== after.agent_markdown ||
      before.soul_markdown !== after.soul_markdown,
    agentMarkdown: {
      before: before.agent_markdown,
      after: after.agent_markdown,
    },
    soulMarkdown: {
      before: before.soul_markdown,
      after: after.soul_markdown,
    },
    modelChanged: before.model_name !== after.model_name,
    model: { before: before.model_name, after: after.model_name },
    toolGroups: setDiff(before.tool_groups, after.tool_groups),
    skills: setDiff(before.skills, after.skills),
    connectorGrants: setDiff(before.connector_grants, after.connector_grants),
  };
}

export function buildDraftReleaseDiff(
  draft: AgentDraft,
  release: AgentRelease | null,
): ReleaseDiff {
  return createDiff(
    release
      ? {
          agent_markdown: release.agent_markdown,
          soul_markdown: release.soul_markdown,
          model_name: release.model_name,
          tool_groups: release.tool_groups,
          skills: releaseSkillNames(release),
          connector_grants: grantNames(release.connector_grants),
        }
      : {
          agent_markdown: "",
          soul_markdown: "",
          model_name: null,
          tool_groups: [],
          skills: [],
          connector_grants: [],
        },
    {
      agent_markdown: draft.agent_markdown,
      soul_markdown: draft.soul_markdown,
      model_name: draft.model_name,
      tool_groups: draft.tool_groups,
      skills: draft.skills.map((skill) => skill.skill_name),
      connector_grants: grantNames(draft.connector_grants),
    },
  );
}

export function buildReleaseDiff(
  before: AgentRelease,
  after: AgentRelease,
): ReleaseDiff {
  return createDiff(
    {
      agent_markdown: before.agent_markdown,
      soul_markdown: before.soul_markdown,
      model_name: before.model_name,
      tool_groups: before.tool_groups,
      skills: releaseSkillNames(before),
      connector_grants: grantNames(before.connector_grants),
    },
    {
      agent_markdown: after.agent_markdown,
      soul_markdown: after.soul_markdown,
      model_name: after.model_name,
      tool_groups: after.tool_groups,
      skills: releaseSkillNames(after),
      connector_grants: grantNames(after.connector_grants),
    },
  );
}

function ChangeList({ title, diff }: { title: string; diff: SetDiff }) {
  const { t } = useI18n();
  const changed = diff.added.length + diff.removed.length > 0;

  return (
    <div className="bg-background rounded-lg border p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <p className="text-xs font-semibold tracking-wide uppercase">{title}</p>
        {changed ? (
          <Badge variant="secondary">
            {diff.added.length + diff.removed.length}
          </Badge>
        ) : (
          <span className="text-muted-foreground flex items-center gap-1 text-xs">
            <CheckIcon className="size-3" />
            {t.publishedAgents.publish.unchanged}
          </span>
        )}
      </div>
      {changed ? (
        <div className="space-y-1.5 font-mono text-xs">
          {diff.added.map((item) => (
            <div
              key={`added-${item}`}
              className="flex items-start gap-2 rounded bg-emerald-500/10 px-2 py-1.5 text-emerald-800 dark:text-emerald-300"
            >
              <ArrowUpIcon className="mt-0.5 size-3 shrink-0" />
              <span className="break-all">
                {t.publishedAgents.publish.added}: {item}
              </span>
            </div>
          ))}
          {diff.removed.map((item) => (
            <div
              key={`removed-${item}`}
              className="flex items-start gap-2 rounded bg-rose-500/10 px-2 py-1.5 text-rose-800 dark:text-rose-300"
            >
              <ArrowDownIcon className="mt-0.5 size-3 shrink-0" />
              <span className="break-all">
                {t.publishedAgents.publish.removed}: {item}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function InstructionDiff({
  title,
  before,
  after,
}: {
  title: string;
  before: string;
  after: string;
}) {
  const { t } = useI18n();
  const changed = before !== after;

  return (
    <div className="bg-background rounded-lg border p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <p className="text-xs font-semibold tracking-wide uppercase">{title}</p>
        {!changed ? (
          <span className="text-muted-foreground flex items-center gap-1 text-xs">
            <CheckIcon className="size-3" />
            {t.publishedAgents.publish.unchanged}
          </span>
        ) : null}
      </div>
      {changed ? (
        <div className="grid gap-2 md:grid-cols-2">
          <pre className="max-h-44 overflow-auto rounded bg-rose-500/8 p-2.5 text-[11px] leading-5 whitespace-pre-wrap">
            {before || "∅"}
          </pre>
          <pre className="max-h-44 overflow-auto rounded bg-emerald-500/8 p-2.5 text-[11px] leading-5 whitespace-pre-wrap">
            {after || "∅"}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

export function ReleaseDiffView({ diff }: { diff: ReleaseDiff }) {
  const { t } = useI18n();

  return (
    <div className="space-y-3" data-testid="release-diff">
      <InstructionDiff
        title="AGENT.md"
        before={diff.agentMarkdown.before}
        after={diff.agentMarkdown.after}
      />
      <InstructionDiff
        title="SOUL.md"
        before={diff.soulMarkdown.before}
        after={diff.soulMarkdown.after}
      />
      <div className="grid gap-3 md:grid-cols-2">
        <div className="bg-background rounded-lg border p-3">
          <p className="mb-2 text-xs font-semibold tracking-wide uppercase">
            {t.publishedAgents.publish.model}
          </p>
          {diff.modelChanged ? (
            <p className="font-mono text-xs">
              <span className="text-rose-700 line-through dark:text-rose-300">
                {diff.model.before ?? t.publishedAgents.publish.defaultModel}
              </span>
              <span className="text-muted-foreground px-2">→</span>
              <span className="text-emerald-700 dark:text-emerald-300">
                {diff.model.after ?? t.publishedAgents.publish.defaultModel}
              </span>
            </p>
          ) : (
            <span className="text-muted-foreground flex items-center gap-1 text-xs">
              <CheckIcon className="size-3" />
              {t.publishedAgents.publish.unchanged}
            </span>
          )}
        </div>
        <ChangeList
          title={t.publishedAgents.publish.toolGroups}
          diff={diff.toolGroups}
        />
        <ChangeList
          title={t.publishedAgents.publish.skills}
          diff={diff.skills}
        />
        <ChangeList
          title={t.publishedAgents.publish.connectorGrants}
          diff={diff.connectorGrants}
        />
      </div>
    </div>
  );
}

function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function ReleaseHistory({
  agentId,
  currentReleaseId,
  releases,
}: {
  agentId: string;
  currentReleaseId: string | null;
  releases: AgentRelease[];
}) {
  const { t } = useI18n();
  const rollback = useRollbackAgent(agentId);
  const [rollbackTarget, setRollbackTarget] = useState<AgentRelease | null>(
    null,
  );
  const [compareFrom, setCompareFrom] = useState<string>("");
  const [compareTo, setCompareTo] = useState<string>("");

  const compared = useMemo(() => {
    const from = releases.find(
      (release) => String(release.release_no) === compareFrom,
    );
    const to = releases.find(
      (release) => String(release.release_no) === compareTo,
    );
    return from && to && from.id !== to.id
      ? { from, to, diff: buildReleaseDiff(from, to) }
      : null;
  }, [compareFrom, compareTo, releases]);

  async function confirmRollback() {
    if (!rollbackTarget) {
      return;
    }
    try {
      await rollback.mutateAsync(rollbackTarget.release_no);
      toast.success(
        t.publishedAgents.publish.rollbackSuccess(rollbackTarget.release_no),
      );
      setRollbackTarget(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <>
      <Card className="shadow-none">
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="bg-muted flex size-9 shrink-0 items-center justify-center rounded-lg">
              <HistoryIcon className="size-4" />
            </div>
            <div>
              <CardTitle className="text-base">
                {t.publishedAgents.publish.historyTitle}
              </CardTitle>
              <p className="text-muted-foreground mt-1 text-sm leading-6">
                {t.publishedAgents.publish.historyDescription}
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          {releases.length > 1 ? (
            <div className="bg-muted/20 rounded-lg border p-4">
              <div className="mb-3 flex items-center gap-2">
                <GitCompareArrowsIcon className="size-4" />
                <p className="text-sm font-semibold">
                  {t.publishedAgents.publish.compareTitle}
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Select value={compareFrom} onValueChange={setCompareFrom}>
                  <SelectTrigger
                    aria-label={t.publishedAgents.publish.compareFrom}
                  >
                    <SelectValue
                      placeholder={t.publishedAgents.publish.compareFrom}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {releases.map((release) => (
                      <SelectItem
                        key={release.id}
                        value={String(release.release_no)}
                      >
                        {t.publishedAgents.gallery.release(release.release_no)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select value={compareTo} onValueChange={setCompareTo}>
                  <SelectTrigger
                    aria-label={t.publishedAgents.publish.compareTo}
                  >
                    <SelectValue
                      placeholder={t.publishedAgents.publish.compareTo}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {releases.map((release) => (
                      <SelectItem
                        key={release.id}
                        value={String(release.release_no)}
                      >
                        {t.publishedAgents.gallery.release(release.release_no)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {compared ? (
                <div className="mt-4">
                  <p className="text-muted-foreground mb-3 text-xs font-medium">
                    {t.publishedAgents.publish.fromTo(
                      compared.from.release_no,
                      compared.to.release_no,
                    )}
                  </p>
                  <ReleaseDiffView diff={compared.diff} />
                </div>
              ) : (
                <p className="text-muted-foreground mt-3 text-xs">
                  {t.publishedAgents.publish.compareDescription}
                </p>
              )}
            </div>
          ) : null}

          <div className="divide-y rounded-lg border">
            {releases.length === 0 ? (
              <p className="text-muted-foreground p-6 text-center text-sm">
                {t.publishedAgents.publish.historyEmpty}
              </p>
            ) : (
              releases.map((release) => {
                const isCurrent = release.id === currentReleaseId;
                return (
                  <div
                    key={release.id}
                    className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="font-mono text-sm font-semibold">
                          {t.publishedAgents.gallery.release(
                            release.release_no,
                          )}
                        </p>
                        {isCurrent ? (
                          <Badge>{t.publishedAgents.publish.current}</Badge>
                        ) : null}
                      </div>
                      <p className="text-muted-foreground mt-1 text-xs">
                        {formatTimestamp(release.created_at)} ·{" "}
                        {t.publishedAgents.publish.createdBy}{" "}
                        <span className="font-mono">{release.created_by}</span>
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={isCurrent}
                      onClick={() => setRollbackTarget(release)}
                    >
                      <RotateCcwIcon />
                      {t.publishedAgents.publish.rollback}
                    </Button>
                  </div>
                );
              })
            )}
          </div>
        </CardContent>
      </Card>

      <Dialog
        open={Boolean(rollbackTarget)}
        onOpenChange={(open) => {
          if (!open) {
            setRollbackTarget(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t.publishedAgents.publish.rollbackTitle(
                rollbackTarget?.release_no ?? 0,
              )}
            </DialogTitle>
            <DialogDescription>
              {t.publishedAgents.publish.rollbackDescription}
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm leading-6 text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100">
            {t.publishedAgents.publish.stableIntegrationNotice}
          </div>
          <Separator />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRollbackTarget(null)}>
              {t.publishedAgents.publish.cancel}
            </Button>
            <Button
              disabled={rollback.isPending}
              onClick={() => void confirmRollback()}
            >
              <RotateCcwIcon />
              {rollback.isPending
                ? t.publishedAgents.publish.rollingBack
                : t.publishedAgents.publish.confirmRollback}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
