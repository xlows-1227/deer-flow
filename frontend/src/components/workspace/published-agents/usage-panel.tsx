"use client";

import {
  ActivityIcon,
  BarChart3Icon,
  CircleAlertIcon,
  ClockIcon,
  DollarSignIcon,
  GaugeIcon,
  PlugIcon,
  ShieldXIcon,
  WifiIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useI18n } from "@/core/i18n/hooks";
import {
  useAgentAuditEvents,
  useAgentKeys,
  useAgentUsage,
  type AgentAuditEvent,
  type AgentUsageDay,
  type PublishedRunSource,
} from "@/core/published-agents";

function errorCount(day: AgentUsageDay): number {
  return Object.entries(day.statuses).reduce(
    (total, [status, count]) => (status === "success" ? total : total + count),
    0,
  );
}

function errorRate(day: AgentUsageDay): number {
  return day.runs > 0 ? (errorCount(day) / day.runs) * 100 : 0;
}

function DailyMetricChart({
  title,
  days,
  value,
  format,
}: {
  title: string;
  days: AgentUsageDay[];
  value: (day: AgentUsageDay) => number;
  format: (value: number) => string;
}) {
  const values = days.map(value);
  const max = Math.max(...values, 1);

  return (
    <div className="bg-background rounded-lg border p-4">
      <p className="mb-4 text-xs font-semibold tracking-wide uppercase">
        {title}
      </p>
      {days.length === 0 ? (
        <div className="text-muted-foreground flex h-36 items-center justify-center text-sm">
          —
        </div>
      ) : (
        <>
          <div
            className="flex h-36 items-end gap-1"
            role="img"
            aria-label={title}
          >
            {days.map((day) => {
              const metric = value(day);
              return (
                <div
                  key={day.date}
                  className="group relative flex h-full min-w-0 flex-1 items-end"
                  title={`${day.date}: ${format(metric)}`}
                >
                  <div
                    className="bg-primary/75 group-hover:bg-primary min-h-0.5 w-full rounded-t-sm transition-colors"
                    style={{
                      height: `${Math.max(2, (metric / max) * 100)}%`,
                    }}
                  />
                </div>
              );
            })}
          </div>
          <div className="text-muted-foreground mt-2 flex justify-between font-mono text-[10px]">
            <span>{days[0]?.date.slice(5)}</span>
            <span>{days.at(-1)?.date.slice(5)}</span>
          </div>
        </>
      )}
    </div>
  );
}

function AuditCategoryBadge({
  category,
}: {
  category: AgentAuditEvent["category"];
}) {
  const { t } = useI18n();
  return (
    <Badge
      variant={
        category === "quota" || category === "authentication"
          ? "destructive"
          : "secondary"
      }
    >
      {t.publishedAgents.ops.auditCategory(category)}
    </Badge>
  );
}

function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatCost(microusd: number): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  }).format(microusd / 1_000_000);
}

export function UsagePanel({ agentId }: { agentId: string }) {
  const { t } = useI18n();
  const [range, setRange] = useState(30);
  const [source, setSource] = useState<PublishedRunSource | undefined>();
  const [keyId, setKeyId] = useState<string | undefined>();
  const { keys } = useAgentKeys(agentId);
  const { usage, isLoading } = useAgentUsage(agentId, range, {
    source,
    keyId,
  });
  const { events, isLoading: auditLoading } = useAgentAuditEvents(agentId, 20);

  const totals = usage?.totals;
  const operations = usage?.operations;
  const totalErrors = useMemo(
    () => usage?.days.reduce((sum, day) => sum + errorCount(day), 0) ?? 0,
    [usage],
  );
  const totalErrorRate =
    totals && totals.runs > 0 ? (totalErrors / totals.runs) * 100 : 0;

  return (
    <div className="space-y-5">
      <Card className="shadow-none">
        <CardHeader>
          <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
            <div className="flex items-start gap-3">
              <div className="bg-muted flex size-9 shrink-0 items-center justify-center rounded-lg">
                <BarChart3Icon className="size-4" />
              </div>
              <div>
                <CardTitle className="text-base">
                  {t.publishedAgents.ops.usageTitle}
                </CardTitle>
                <p className="text-muted-foreground mt-1 text-sm leading-6">
                  {t.publishedAgents.ops.usageDescription}
                </p>
              </div>
            </div>
            <div className="grid gap-2 sm:grid-cols-3">
              <Select
                value={String(range)}
                onValueChange={(value) => setRange(Number(value))}
              >
                <SelectTrigger aria-label={t.publishedAgents.ops.dateRange}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {[7, 30, 90].map((days) => (
                    <SelectItem key={days} value={String(days)}>
                      {t.publishedAgents.ops.lastDays(days)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={source ?? "all"}
                onValueChange={(value) => {
                  const next =
                    value === "all" ? undefined : (value as PublishedRunSource);
                  setSource(next);
                  if (next !== "api") {
                    setKeyId(undefined);
                  }
                }}
              >
                <SelectTrigger aria-label={t.publishedAgents.ops.source}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">
                    {t.publishedAgents.ops.allSources}
                  </SelectItem>
                  <SelectItem value="api">API</SelectItem>
                  <SelectItem value="feishu">Feishu</SelectItem>
                </SelectContent>
              </Select>
              <Select
                value={keyId ?? "all"}
                disabled={source !== "api"}
                onValueChange={(value) =>
                  setKeyId(value === "all" ? undefined : value)
                }
              >
                <SelectTrigger aria-label={t.publishedAgents.ops.apiKey}>
                  <SelectValue placeholder={t.publishedAgents.ops.allKeys} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">
                    {t.publishedAgents.ops.allKeys}
                  </SelectItem>
                  {keys.map((key) => (
                    <SelectItem key={key.id} value={key.id}>
                      {key.name} · {key.last_four}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {isLoading ? (
            <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
              <Skeleton className="h-24" />
              <Skeleton className="h-24" />
              <Skeleton className="h-24" />
              <Skeleton className="h-24" />
              <Skeleton className="h-24" />
              <Skeleton className="h-24" />
            </div>
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="bg-muted/15 rounded-lg border p-4">
                  <p className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                    <ActivityIcon className="size-3.5" />
                    {t.publishedAgents.ops.totalRuns}
                  </p>
                  <p className="mt-2 text-2xl font-semibold tabular-nums">
                    {(totals?.runs ?? 0).toLocaleString()}
                  </p>
                </div>
                <div className="bg-muted/15 rounded-lg border p-4">
                  <p className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                    <GaugeIcon className="size-3.5" />
                    {t.publishedAgents.ops.totalTokens}
                  </p>
                  <p className="mt-2 text-2xl font-semibold tabular-nums">
                    {(totals?.total_tokens ?? 0).toLocaleString()}
                  </p>
                </div>
                <div className="bg-muted/15 rounded-lg border p-4">
                  <p className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                    <CircleAlertIcon className="size-3.5" />
                    {t.publishedAgents.ops.errorRate}
                  </p>
                  <p className="mt-2 text-2xl font-semibold tabular-nums">
                    {totalErrorRate.toFixed(1)}%
                  </p>
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
                <div className="bg-muted/15 rounded-lg border p-4">
                  <p className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                    <DollarSignIcon className="size-3.5" />
                    {t.publishedAgents.ops.estimatedCost}
                  </p>
                  <p className="mt-2 text-lg font-semibold tabular-nums">
                    {formatCost(totals?.cost_microusd ?? 0)}
                  </p>
                </div>
                <div className="bg-muted/15 rounded-lg border p-4">
                  <p className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                    <CircleAlertIcon className="size-3.5" />
                    {t.publishedAgents.ops.currentReleaseErrorRate}
                  </p>
                  <p className="mt-2 text-lg font-semibold tabular-nums">
                    {(
                      (operations?.current_release_error_rate ?? 0) * 100
                    ).toFixed(1)}
                    %
                  </p>
                </div>
                <div className="bg-muted/15 rounded-lg border p-4">
                  <p className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                    <GaugeIcon className="size-3.5" />
                    {t.publishedAgents.ops.quotaRejections}
                  </p>
                  <p className="mt-2 text-lg font-semibold tabular-nums">
                    {operations?.quota_rejections ?? 0}
                    <span className="text-muted-foreground ml-1 text-xs font-normal">
                      / {operations?.concurrency_saturation ?? 0}{" "}
                      {t.publishedAgents.ops.saturation}
                    </span>
                  </p>
                </div>
                <div className="bg-muted/15 rounded-lg border p-4">
                  <p className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                    <ClockIcon className="size-3.5" />
                    {t.publishedAgents.ops.feishuP95Latency}
                  </p>
                  <p className="mt-2 text-lg font-semibold tabular-nums">
                    {(
                      operations?.feishu_event_latency_ms.p95 ?? 0
                    ).toLocaleString()}{" "}
                    ms
                  </p>
                </div>
                <div className="bg-muted/15 rounded-lg border p-4">
                  <p className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                    <PlugIcon className="size-3.5" />
                    {t.publishedAgents.ops.connectorIssues}
                  </p>
                  <p className="mt-2 text-lg font-semibold tabular-nums">
                    {operations?.connector_failures ?? 0}
                    <span className="text-muted-foreground ml-1 text-xs font-normal">
                      / {operations?.connector_denials ?? 0}{" "}
                      {t.publishedAgents.ops.denied}
                    </span>
                  </p>
                </div>
                <div className="bg-muted/15 rounded-lg border p-4">
                  <p className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                    <WifiIcon className="size-3.5" />
                    {t.publishedAgents.ops.bindingHealth}
                  </p>
                  <p className="mt-2 text-lg font-semibold tabular-nums">
                    {operations?.active_bindings ?? 0}
                    <span className="text-muted-foreground ml-1 text-xs font-normal">
                      / {operations?.unhealthy_bindings ?? 0}{" "}
                      {t.publishedAgents.ops.unhealthy}
                    </span>
                  </p>
                </div>
              </div>
              <div className="grid gap-3 xl:grid-cols-3">
                <DailyMetricChart
                  title={t.publishedAgents.ops.dailyRuns}
                  days={usage?.days ?? []}
                  value={(day) => day.runs}
                  format={(value) => value.toLocaleString()}
                />
                <DailyMetricChart
                  title={t.publishedAgents.ops.dailyTokens}
                  days={usage?.days ?? []}
                  value={(day) => day.total_tokens}
                  format={(value) => value.toLocaleString()}
                />
                <DailyMetricChart
                  title={t.publishedAgents.ops.dailyErrorRate}
                  days={usage?.days ?? []}
                  value={errorRate}
                  format={(value) => `${value.toFixed(1)}%`}
                />
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card className="shadow-none">
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="bg-muted flex size-9 shrink-0 items-center justify-center rounded-lg">
              <ShieldXIcon className="size-4" />
            </div>
            <div>
              <CardTitle className="text-base">
                {t.publishedAgents.ops.auditTitle}
              </CardTitle>
              <p className="text-muted-foreground mt-1 text-sm leading-6">
                {t.publishedAgents.ops.auditDescription}
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="divide-y rounded-lg border">
            {auditLoading ? (
              <p className="text-muted-foreground p-5 text-sm">
                {t.publishedAgents.ops.loadingAudit}
              </p>
            ) : events.length === 0 ? (
              <p className="text-muted-foreground p-5 text-sm">
                {t.publishedAgents.ops.noRejections}
              </p>
            ) : (
              events.map((event) => (
                <div
                  key={event.id}
                  className="flex flex-col gap-3 p-4 xl:flex-row xl:items-center xl:justify-between"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <AuditCategoryBadge category={event.category} />
                      <Badge variant="outline">{event.status_code}</Badge>
                      <span className="font-mono text-xs font-medium">
                        {event.method}
                      </span>
                      <span className="truncate font-mono text-xs">
                        {event.path_template}
                      </span>
                    </div>
                    <p className="text-muted-foreground mt-2 text-xs">
                      {formatTimestamp(event.created_at)} ·{" "}
                      {event.source ?? "—"} · {event.credential_id ?? "—"} ·{" "}
                      {event.duration_ms} ms
                    </p>
                  </div>
                  <code className="text-muted-foreground shrink-0 text-[10px]">
                    {event.request_id}
                  </code>
                </div>
              ))
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
