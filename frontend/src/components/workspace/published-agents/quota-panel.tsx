"use client";

import {
  GaugeIcon,
  SaveIcon,
  ShieldAlertIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useI18n } from "@/core/i18n/hooks";
import {
  DraftRevisionConflictError,
  useAgentQuotaPolicy,
  useUpdateAgentDraft,
  type AgentDraft,
  type QuotaOverrides,
} from "@/core/published-agents";

import {
  createEmptyQuotaInput,
  parseQuotaInput,
  QuotaFieldInput,
  quotaFields,
  quotaOverridesToInput,
  type QuotaInput,
} from "./quota-fields";

export function QuotaPanel({
  agentId,
  draft,
  hasUnsavedChanges,
}: {
  agentId: string;
  draft: AgentDraft;
  hasUnsavedChanges: boolean;
}) {
  const { t } = useI18n();
  const { data: policy, isLoading, error } = useAgentQuotaPolicy(agentId);
  const updateDraft = useUpdateAgentDraft(agentId);
  const [input, setInput] = useState<QuotaInput>(createEmptyQuotaInput);

  useEffect(() => {
    if (policy) {
      setInput(quotaOverridesToInput(policy.owner_overrides));
    }
  }, [policy]);

  const parsed = useMemo(
    () =>
      policy
        ? parseQuotaInput(input, policy.platform_defaults)
        : { overrides: {}, errors: {} },
    [input, policy],
  );
  const effectivePreview = useMemo(() => {
    if (!policy) {
      return null;
    }
    const values = Object.fromEntries(
      quotaFields.map((field) => [
        field,
        parsed.overrides[field] ?? policy.platform_defaults[field],
      ]),
    ) as Required<QuotaOverrides>;
    values.max_tokens_per_run = Math.min(
      values.max_tokens_per_run,
      values.daily_tokens,
    );
    return values;
  }, [parsed.overrides, policy]);
  const hasErrors = Object.keys(parsed.errors).length > 0;
  const changed =
    policy !== undefined &&
    JSON.stringify(parsed.overrides) !== JSON.stringify(policy.owner_overrides);

  async function save() {
    try {
      await updateDraft.mutateAsync({
        revision: draft.revision,
        quota_overrides: parsed.overrides,
      });
      toast.success(t.publishedAgents.ops.quotaSaved);
    } catch (saveError) {
      if (saveError instanceof DraftRevisionConflictError) {
        toast.error(t.publishedAgents.ops.quotaConflict);
        return;
      }
      toast.error(
        saveError instanceof Error ? saveError.message : String(saveError),
      );
    }
  }

  return (
    <Card className="shadow-none">
      <CardHeader>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex items-start gap-3">
            <div className="bg-muted flex size-9 shrink-0 items-center justify-center rounded-lg">
              <GaugeIcon className="size-4" />
            </div>
            <div>
              <CardTitle className="text-base">
                {t.publishedAgents.ops.quotaTitle}
              </CardTitle>
              <p className="text-muted-foreground mt-1 text-sm leading-6">
                {t.publishedAgents.ops.quotaDescription}
              </p>
            </div>
          </div>
          <Button
            disabled={
              hasUnsavedChanges ||
              !changed ||
              hasErrors ||
              updateDraft.isPending
            }
            onClick={() => void save()}
          >
            <SaveIcon />
            {updateDraft.isPending
              ? t.publishedAgents.ops.savingQuota
              : t.publishedAgents.ops.saveQuota}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <Alert>
          <ShieldAlertIcon />
          <AlertTitle>{t.publishedAgents.ops.inheritanceTitle}</AlertTitle>
          <AlertDescription>
            {t.publishedAgents.ops.inheritanceDescription}
          </AlertDescription>
        </Alert>
        {hasUnsavedChanges ? (
          <Alert className="border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100">
            <TriangleAlertIcon />
            <AlertTitle>{t.publishedAgents.ops.saveOtherDraftTitle}</AlertTitle>
            <AlertDescription>
              {t.publishedAgents.ops.saveOtherDraftDescription}
            </AlertDescription>
          </Alert>
        ) : null}
        {error ? (
          <Alert variant="destructive">
            <TriangleAlertIcon />
            <AlertTitle>{t.publishedAgents.ops.quotaLoadError}</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : String(error)}
            </AlertDescription>
          </Alert>
        ) : null}
        {isLoading || !policy ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {quotaFields.slice(0, 6).map((field) => (
              <Skeleton key={field} className="h-36" />
            ))}
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {quotaFields.map((field) => {
              const fieldError = parsed.errors[field];
              const inherited = !input[field].trim();
              return (
                <div key={field} className="bg-muted/10 rounded-lg border p-4">
                  <div className="mb-3 flex justify-end">
                    <Badge variant={inherited ? "secondary" : "outline"}>
                      {inherited
                        ? t.publishedAgents.ops.inherited
                        : t.publishedAgents.ops.overridden}
                    </Badge>
                  </div>
                  <QuotaFieldInput
                    id={`agent-quota-${field}`}
                    field={field}
                    value={input[field]}
                    label={t.publishedAgents.ops.quotaOverrideLabel(field)}
                    placeholder={String(policy.platform_defaults[field])}
                    invalid={Boolean(fieldError)}
                    maximum={policy.platform_defaults[field]}
                    onChange={(changedField, nextValue) =>
                      setInput((current) => ({
                        ...current,
                        [changedField]: nextValue,
                      }))
                    }
                  />
                  <div className="text-muted-foreground mt-3 grid grid-cols-2 gap-2 text-[11px]">
                    <div>
                      <p>{t.publishedAgents.ops.platformDefault}</p>
                      <p className="text-foreground mt-0.5 font-mono">
                        {policy.platform_defaults[field].toLocaleString()}
                      </p>
                    </div>
                    <div>
                      <p>{t.publishedAgents.ops.effectiveAfterPublish}</p>
                      <p className="text-foreground mt-0.5 font-mono">
                        {(fieldError
                          ? policy.effective[field]
                          : (effectivePreview?.[field] ??
                            policy.effective[field])
                        ).toLocaleString()}
                      </p>
                    </div>
                  </div>
                  {fieldError ? (
                    <p className="text-destructive mt-2 text-xs">
                      {fieldError === "maximum"
                        ? t.publishedAgents.ops.exceedsMaximum(
                            policy.platform_defaults[field],
                          )
                        : t.publishedAgents.ops.positiveInteger}
                    </p>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
        <p className="text-muted-foreground text-xs leading-5">
          {t.publishedAgents.ops.draftOnlyNotice}
        </p>
      </CardContent>
    </Card>
  );
}
