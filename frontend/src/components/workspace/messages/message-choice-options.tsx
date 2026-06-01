"use client";

import { ArrowRightIcon, MousePointerClickIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { MessageChoiceOption } from "@/core/messages/choice-options";
import { cn } from "@/lib/utils";

export function MessageChoiceOptions({
  options,
  disabled,
  onSelect,
}: {
  options: MessageChoiceOption[];
  disabled?: boolean;
  onSelect?: (value: string) => void;
}) {
  return (
    <div className="mt-3 w-full max-w-2xl">
      <div className="text-muted-foreground mb-2 flex items-center gap-2 text-xs font-medium">
        <MousePointerClickIcon className="size-3.5" />
        <span>请选择一个选项</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {options.map((option) => (
          <Button
            key={`${option.index}-${option.value}`}
            type="button"
            variant="outline"
            disabled={disabled}
            onClick={() => onSelect?.(option.value)}
            className={cn(
              "group border-border/70 bg-background/80 h-auto min-h-12 w-full justify-start rounded-lg px-3 py-2.5 text-left shadow-xs",
              "hover:border-primary/35 hover:bg-primary/5 hover:shadow-sm",
              "dark:bg-background/55 dark:hover:bg-primary/10",
            )}
          >
            <span className="bg-muted text-muted-foreground group-hover:bg-primary group-hover:text-primary-foreground flex size-6 shrink-0 items-center justify-center rounded-md text-[11px] font-semibold transition-colors">
              {option.index}
            </span>
            <span className="min-w-0 flex-1 text-sm leading-snug font-medium break-words whitespace-normal">
              {option.value}
            </span>
            {!disabled && (
              <ArrowRightIcon className="text-muted-foreground group-hover:text-primary size-4 transition-transform group-hover:translate-x-0.5" />
            )}
          </Button>
        ))}
      </div>
    </div>
  );
}
