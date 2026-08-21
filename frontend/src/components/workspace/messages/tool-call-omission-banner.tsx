"use client";

import { ChevronDownIcon, ChevronRightIcon, WrenchIcon } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export function ToolCallOmissionBanner({
  count,
  toolNames,
  className,
}: {
  count: number;
  toolNames?: string[][];
  className?: string;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);

  if (count <= 0) return null;

  const resolvedNames = toolNames?.slice(0, count) ?? [];
  const badgeNames = resolvedNames
    .map((names, i) => (names.length > 0 ? names : [`#${i + 1}`]))
    .flat();

  return (
    <div
      data-testid="tool-call-omission-banner"
      className={cn(
        "mb-2 overflow-hidden rounded-lg border border-amber-200/70 bg-amber-50/60",
        className,
      )}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-amber-100/60"
      >
        {expanded ? (
          <ChevronDownIcon className="size-4 shrink-0 text-slate-500" />
        ) : (
          <ChevronRightIcon className="size-4 shrink-0 text-slate-500" />
        )}
        <WrenchIcon className="size-4 shrink-0 text-amber-600" />
        <span className="text-sm font-medium text-slate-800">
          {t.toolCalls.toolCallOmitted(count)}
        </span>
        <Badge
          variant="secondary"
          className="ml-auto bg-amber-100 text-amber-700"
        >
          {count}
        </Badge>
        <span className="text-muted-foreground text-xs">
          {expanded ? t.toolCalls.collapseToolCalls : t.toolCalls.expandToolCalls}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-amber-200/70 bg-white/50 px-3 py-2">
          <p className="text-xs text-slate-500">
            {t.toolCalls.toolCallOmittedDetail}
          </p>
          <div className="mt-2 flex flex-wrap gap-1">
            {badgeNames.map((name, i) => (
              <span
                key={i}
                title={name.startsWith("#") ? `工具调用 ${name}` : name}
                className="inline-flex items-center rounded border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700"
              >
                <span className={cn(name.startsWith("#") ? "" : "font-mono")}>
                  {name}
                </span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
