"use client";

import { Archive, ChevronDownIcon, ChevronUpIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { MarkdownContent } from "./markdown-content";

export interface CompactionSummaryData {
  summary: string;
  compacted_message_ids: string[];
  preserved_message_count: number;
  total_tokens_before: number;
  read_files: string[];
  modified_files: string[];
}

export function CompactionSummary({
  data,
  className,
}: {
  data: CompactionSummaryData;
  className?: string;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);

  const savedMessages = data.compacted_message_ids.length;
  const savedTokens = data.total_tokens_before;

  return (
    <div
      className={cn(
        "my-4 flex flex-col gap-2 rounded-lg border border-amber-200/60 bg-amber-50/50 px-4 py-3 dark:border-amber-900/40 dark:bg-amber-950/20",
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <Archive className="size-4 text-amber-600 dark:text-amber-400" />
        <span className="text-sm font-medium text-amber-800 dark:text-amber-300">
          {t.compaction?.title ?? "Context Compacted"}
        </span>
        <span className="text-muted-foreground ml-auto text-xs">
          {savedMessages > 0
            ? `${savedMessages} messages - ~${savedTokens.toLocaleString()} tokens`
            : ""}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-6 px-2 text-xs"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? (
            <ChevronUpIcon className="size-3" />
          ) : (
            <ChevronDownIcon className="size-3" />
          )}
        </Button>
      </div>

      {expanded && (
        <div className="space-y-3">
          {data.summary && (
            <div className="prose-compact prose-sm max-w-none text-slate-700 dark:text-slate-300">
              <MarkdownContent content={data.summary} isLoading={false} />
            </div>
          )}

          {(data.read_files.length > 0 || data.modified_files.length > 0) && (
            <div className="space-y-1 text-xs">
              {data.read_files.length > 0 && (
                <div>
                  <span className="text-muted-foreground font-medium">
                    {t.compaction?.readFiles ?? "Read"}:
                  </span>{" "}
                  <span className="text-muted-foreground">
                    {data.read_files.join(", ")}
                  </span>
                </div>
              )}
              {data.modified_files.length > 0 && (
                <div>
                  <span className="text-muted-foreground font-medium">
                    {t.compaction?.modifiedFiles ?? "Modified"}:
                  </span>{" "}
                  <span className="text-muted-foreground">
                    {data.modified_files.join(", ")}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
