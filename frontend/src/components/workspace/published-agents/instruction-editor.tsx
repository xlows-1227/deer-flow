"use client";

import {
  BrainIcon,
  CheckIcon,
  CompassIcon,
  FileTextIcon,
  SparklesIcon,
  TriangleAlertIcon,
  ZapIcon,
  type LucideIcon,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";
import {
  createSoulPresetMarkdown,
  detectSoulPresetId,
  SOUL_PRESET_IDS,
  type SoulPresetContentMap,
  type SoulPresetId,
} from "@/core/published-agents/instructions";
import { cn } from "@/lib/utils";

interface InstructionEditorProps {
  agentMarkdown: string;
  soulMarkdown: string;
  onAgentMarkdownChange: (value: string) => void;
  onSoulMarkdownChange: (value: string) => void;
}

const presetVisuals: Record<
  SoulPresetId,
  { icon: LucideIcon; iconClassName: string }
> = {
  professional: {
    icon: SparklesIcon,
    iconClassName: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  },
  warm: {
    icon: BrainIcon,
    iconClassName: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  concise: {
    icon: ZapIcon,
    iconClassName: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  coach: {
    icon: CompassIcon,
    iconClassName: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
  },
};

export function InstructionEditor({
  agentMarkdown,
  soulMarkdown,
  onAgentMarkdownChange,
  onSoulMarkdownChange,
}: InstructionEditorProps) {
  const { t } = useI18n();
  const presets = t.publishedAgents.studio.soulPresets;
  const soulPresetContents: SoulPresetContentMap = {
    professional: presets.professional.content,
    warm: presets.warm.content,
    concise: presets.concise.content,
    coach: presets.coach.content,
  };
  const selectedPresetId = detectSoulPresetId(soulMarkdown, soulPresetContents);
  const hasLegacyCustomSoul =
    soulMarkdown.trim().length > 0 && selectedPresetId === null;

  return (
    <div className="space-y-5">
      <section className="bg-card overflow-hidden rounded-xl border">
        <div className="bg-muted/25 flex items-start justify-between gap-4 border-b px-4 py-3">
          <div className="flex min-w-0 items-start gap-3">
            <div className="bg-background ring-border flex size-9 shrink-0 items-center justify-center rounded-lg ring-1">
              <FileTextIcon className="size-4" />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <h3 className="text-sm font-semibold">
                  {t.publishedAgents.studio.agentMarkdownTitle}
                </h3>
                <span className="text-muted-foreground font-mono text-[11px]">
                  AGENT.md
                </span>
              </div>
              <p className="text-muted-foreground mt-0.5 text-xs leading-5">
                {t.publishedAgents.studio.agentMarkdownDescription}
              </p>
            </div>
          </div>
          <Badge variant="outline" className="font-mono font-normal">
            Markdown
          </Badge>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 border-b px-4 py-2.5">
          <span className="text-muted-foreground mr-1 text-[10px] font-semibold tracking-wide uppercase">
            {t.publishedAgents.studio.instructionSuggestionsLabel}
          </span>
          {t.publishedAgents.studio.agentMarkdownSuggestions.map(
            (suggestion) => (
              <Badge
                key={suggestion}
                variant="secondary"
                className="font-normal"
              >
                {suggestion}
              </Badge>
            ),
          )}
        </div>

        <Textarea
          id="published-agent-agent-markdown"
          aria-label={`${t.publishedAgents.studio.agentMarkdownTitle} (AGENT.md)`}
          value={agentMarkdown}
          spellCheck={false}
          onChange={(event) => onAgentMarkdownChange(event.target.value)}
          className="min-h-[32rem] resize-y rounded-none border-0 bg-transparent px-5 py-4 font-mono text-[13px] leading-6 shadow-none focus-visible:ring-0"
          placeholder={t.publishedAgents.studio.agentMarkdownPlaceholder}
        />
      </section>

      <section className="bg-card overflow-hidden rounded-xl border">
        <div className="bg-muted/25 flex items-start justify-between gap-4 border-b px-4 py-3">
          <div className="flex min-w-0 items-start gap-3">
            <div className="bg-background ring-border flex size-9 shrink-0 items-center justify-center rounded-lg ring-1">
              <SparklesIcon className="size-4" />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <h3 className="text-sm font-semibold">
                  {t.publishedAgents.studio.soulPresetTitle}
                </h3>
                <span className="text-muted-foreground font-mono text-[11px]">
                  SOUL.md
                </span>
              </div>
              <p className="text-muted-foreground mt-0.5 text-xs leading-5">
                {t.publishedAgents.studio.soulPresetDescription}
              </p>
            </div>
          </div>
          <Badge variant="secondary" className="shrink-0 font-normal">
            {t.publishedAgents.studio.soulPresetBadge}
          </Badge>
        </div>

        <div className="space-y-4 p-4">
          {hasLegacyCustomSoul ? (
            <Alert className="border-amber-300 bg-amber-50/80 dark:border-amber-800 dark:bg-amber-950/40">
              <TriangleAlertIcon className="text-amber-700 dark:text-amber-300" />
              <AlertTitle>
                {t.publishedAgents.studio.soulPresetLegacyTitle}
              </AlertTitle>
              <AlertDescription className="space-y-3">
                <p>{t.publishedAgents.studio.soulPresetLegacyDescription}</p>
                <div>
                  <p className="mb-1.5 text-xs font-medium">
                    {t.publishedAgents.studio.soulPresetLegacyPreviewLabel}
                  </p>
                  <pre
                    aria-label={
                      t.publishedAgents.studio.soulPresetLegacyPreviewLabel
                    }
                    className="border-border/70 bg-background/70 max-h-32 overflow-auto rounded-md border p-3 font-mono text-xs leading-5 whitespace-pre-wrap"
                  >
                    {soulMarkdown}
                  </pre>
                </div>
              </AlertDescription>
            </Alert>
          ) : null}

          <div
            role="radiogroup"
            aria-label={t.publishedAgents.studio.soulPresetTitle}
            className="grid gap-3 sm:grid-cols-2"
          >
            {SOUL_PRESET_IDS.map((presetId) => {
              const preset = presets[presetId];
              const visual = presetVisuals[presetId];
              const Icon = visual.icon;
              const selected = presetId === selectedPresetId;

              return (
                <button
                  key={presetId}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  onClick={() =>
                    onSoulMarkdownChange(
                      createSoulPresetMarkdown(presetId, preset.content),
                    )
                  }
                  className={cn(
                    "hover:bg-muted/35 focus-visible:ring-ring relative flex min-h-24 items-start gap-3 rounded-lg border p-4 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none",
                    selected
                      ? "border-primary bg-primary/[0.04] shadow-[inset_0_0_0_1px_var(--primary)]"
                      : "border-border",
                  )}
                >
                  <span
                    className={cn(
                      "flex size-9 shrink-0 items-center justify-center rounded-lg",
                      visual.iconClassName,
                    )}
                  >
                    <Icon className="size-4" />
                  </span>
                  <span className="min-w-0 pr-6">
                    <span className="block text-sm font-semibold">
                      {preset.name}
                    </span>
                    <span className="text-muted-foreground mt-1 block text-xs leading-5">
                      {preset.summary}
                    </span>
                  </span>
                  {selected ? (
                    <span className="bg-primary text-primary-foreground absolute top-3 right-3 flex size-5 items-center justify-center rounded-full">
                      <CheckIcon className="size-3" />
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
        </div>
      </section>
    </div>
  );
}
