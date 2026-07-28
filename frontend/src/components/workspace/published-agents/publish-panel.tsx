"use client";

import {
  CircleCheckBigIcon,
  LoaderCircleIcon,
  RocketIcon,
  ShieldAlertIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useI18n } from "@/core/i18n/hooks";
import {
  PublishValidationError,
  useAgentReleases,
  usePublishAgent,
  type AgentDraft,
  type PublishViolation,
  type PublishedAgent,
} from "@/core/published-agents";

import {
  buildDraftReleaseDiff,
  ReleaseDiffView,
  ReleaseHistory,
} from "./release-history";

export function PublishPanel({
  agent,
  draft,
  hasUnsavedChanges,
}: {
  agent: PublishedAgent;
  draft: AgentDraft;
  hasUnsavedChanges: boolean;
}) {
  const { t } = useI18n();
  const { releases, isLoading, error } = useAgentReleases(agent.id);
  const publish = usePublishAgent(agent.id);
  const [violations, setViolations] = useState<PublishViolation[]>([]);
  const [publishedReleaseNo, setPublishedReleaseNo] = useState<number | null>(
    null,
  );

  const currentRelease = useMemo(
    () =>
      releases.find((release) => release.id === agent.current_release_id) ??
      null,
    [agent.current_release_id, releases],
  );
  const diff = useMemo(
    () => buildDraftReleaseDiff(draft, currentRelease),
    [currentRelease, draft],
  );

  async function publishDraft() {
    setViolations([]);
    setPublishedReleaseNo(null);
    try {
      const result = await publish.mutateAsync();
      setPublishedReleaseNo(result.release_no);
    } catch (publishError) {
      if (publishError instanceof PublishValidationError) {
        setViolations(publishError.violations);
        return;
      }
      setViolations([
        {
          code: "UNKNOWN",
          message:
            publishError instanceof Error
              ? publishError.message
              : String(publishError),
          field: null,
        },
      ]);
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-5">
        <Skeleton className="h-52 w-full" />
        <Skeleton className="h-80 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">
          {t.publishedAgents.publish.title}
        </h2>
        <p className="text-muted-foreground mt-1 text-sm leading-6">
          {t.publishedAgents.publish.description}
        </p>
      </div>

      {error ? (
        <Alert variant="destructive">
          <ShieldAlertIcon />
          <AlertTitle>{t.publishedAgents.publish.loadError}</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : String(error)}
          </AlertDescription>
        </Alert>
      ) : null}

      {hasUnsavedChanges ? (
        <Alert className="border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100">
          <ShieldAlertIcon />
          <AlertTitle>{t.publishedAgents.publish.unsavedTitle}</AlertTitle>
          <AlertDescription>
            {t.publishedAgents.publish.unsavedDescription}
          </AlertDescription>
        </Alert>
      ) : null}

      {publishedReleaseNo !== null ? (
        <Alert className="border-emerald-300 bg-emerald-50 text-emerald-950 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-100">
          <CircleCheckBigIcon />
          <AlertTitle>
            {t.publishedAgents.publish.successTitle(publishedReleaseNo)}
          </AlertTitle>
          <AlertDescription>
            {t.publishedAgents.publish.successDescription}
          </AlertDescription>
        </Alert>
      ) : null}

      {violations.length > 0 ? (
        <Alert variant="destructive" data-testid="publish-violations">
          <ShieldAlertIcon />
          <AlertTitle>{t.publishedAgents.publish.validationTitle}</AlertTitle>
          <AlertDescription>
            <p>{t.publishedAgents.publish.validationDescription}</p>
            <ul className="mt-2 space-y-1.5">
              {violations.map((violation, index) => (
                <li
                  key={`${violation.code}-${violation.field ?? "global"}-${index}`}
                  className="border-destructive/20 bg-background/60 rounded border px-3 py-2"
                >
                  <span className="font-mono text-[10px] font-semibold tracking-wide">
                    {violation.code}
                  </span>
                  <p className="mt-0.5 text-sm">
                    {t.publishedAgents.publish.violation(
                      violation.code,
                      violation.message,
                    )}
                  </p>
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      <Card className="overflow-hidden shadow-none">
        <CardHeader className="bg-muted/20 border-b">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="mb-2 flex items-center gap-2">
                <Badge variant="outline" className="font-mono">
                  {currentRelease
                    ? t.publishedAgents.gallery.release(
                        currentRelease.release_no,
                      )
                    : t.publishedAgents.publish.neverPublished}
                </Badge>
                <span className="text-muted-foreground">→</span>
                <Badge variant="secondary" className="font-mono">
                  {t.publishedAgents.studio.draftVersion(draft.revision)}
                </Badge>
              </div>
              <CardTitle className="text-base">
                {t.publishedAgents.publish.changeSummary}
              </CardTitle>
              <p className="text-muted-foreground mt-1 text-sm">
                {currentRelease
                  ? t.publishedAgents.publish.savedDraftOnly
                  : t.publishedAgents.publish.initialSummary}
              </p>
            </div>
            <Button
              size="lg"
              disabled={
                hasUnsavedChanges ||
                publish.isPending ||
                agent.status === "archived"
              }
              onClick={() => void publishDraft()}
            >
              {publish.isPending ? (
                <LoaderCircleIcon className="animate-spin" />
              ) : (
                <RocketIcon />
              )}
              {publish.isPending
                ? t.publishedAgents.publish.publishing
                : t.publishedAgents.publish.publish}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="pt-6">
          <ReleaseDiffView diff={diff} />
        </CardContent>
      </Card>

      <ReleaseHistory
        agentId={agent.id}
        currentReleaseId={agent.current_release_id}
        releases={releases}
      />
    </div>
  );
}
