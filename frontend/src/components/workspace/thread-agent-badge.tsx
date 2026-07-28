"use client";

import { BotIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export function ThreadAgentBadge({
  agentName,
  className,
}: {
  agentName: string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "border-primary/20 bg-primary/5 text-primary inline-flex min-w-0 shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium",
        className,
      )}
      data-testid="thread-agent-badge"
      title={agentName}
    >
      <BotIcon aria-hidden="true" className="size-3 shrink-0" />
      <span className="truncate">{agentName}</span>
    </span>
  );
}
