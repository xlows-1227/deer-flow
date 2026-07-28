"use client";

import {
  CheckIcon,
  CircleAlertIcon,
  Globe2Icon,
  LockKeyholeIcon,
  PlugZapIcon,
  SearchIcon,
  XIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import type {
  AgentConnectorGrant,
  AgentDraftSkill,
  SelectableAgentSkill,
} from "@/core/published-agents";
import { cn } from "@/lib/utils";

interface SkillPickerProps {
  skills: SelectableAgentSkill[];
  selected: AgentDraftSkill[];
  grants: AgentConnectorGrant[];
  onChange: (skills: AgentDraftSkill[]) => void;
}

function nonEmpty(value: string | null | undefined) {
  const trimmed = value?.trim();
  if (!trimmed) {
    return undefined;
  }
  return trimmed;
}

export function SkillPicker({
  skills,
  selected,
  grants,
  onChange,
}: SkillPickerProps) {
  const { locale, t } = useI18n();
  const [query, setQuery] = useState("");
  const grantedCapabilities = new Set(grants.map((grant) => grant.capability));
  const selectedNames = new Set(selected.map((skill) => skill.skill_name));
  const filteredSkills = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    if (!normalizedQuery) {
      return skills;
    }
    return skills.filter((skill) =>
      [
        skill.skill_name,
        skill.display_name,
        skill.description,
        skill.description_zh,
        skill.source,
        ...(skill.declared_connector_caps ?? []),
      ].some((value) => value?.toLocaleLowerCase().includes(normalizedQuery)),
    );
  }, [query, skills]);
  const hasQuery = query.trim().length > 0;
  const prefersChinese = locale === "zh-CN";

  function toggle(skill: SelectableAgentSkill) {
    if (selectedNames.has(skill.skill_name)) {
      onChange(
        selected.filter((entry) => entry.skill_name !== skill.skill_name),
      );
      return;
    }
    onChange([
      ...selected,
      { skill_name: skill.skill_name, source: skill.source },
    ]);
  }

  function getDisplayName(skill: SelectableAgentSkill) {
    if (prefersChinese) {
      return nonEmpty(skill.display_name) ?? skill.skill_name;
    }
    return skill.skill_name;
  }

  function getDescription(skill: SelectableAgentSkill) {
    const englishDescription = nonEmpty(skill.description);
    const chineseDescription = nonEmpty(skill.description_zh);
    if (prefersChinese) {
      return chineseDescription ?? englishDescription;
    }
    return englishDescription ?? chineseDescription;
  }

  return (
    <div className="space-y-6">
      <div className="bg-muted/20 rounded-xl border p-3">
        <div className="relative">
          <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
          <Input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label={t.publishedAgents.studio.skillSearchLabel}
            placeholder={t.publishedAgents.studio.skillSearchPlaceholder}
            className="bg-background h-10 pr-10 pl-9"
          />
          {query ? (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label={t.publishedAgents.studio.clearSkillSearch}
              className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 absolute top-1/2 right-2 flex size-7 -translate-y-1/2 items-center justify-center rounded-md transition-colors outline-none focus-visible:ring-3"
            >
              <XIcon className="size-4" />
            </button>
          ) : null}
        </div>
        <p className="text-muted-foreground mt-2 px-1 text-xs">
          {t.publishedAgents.studio.skillSearchSummary(
            filteredSkills.length,
            skills.length,
            selected.length,
          )}
        </p>
      </div>

      {hasQuery && filteredSkills.length === 0 ? (
        <div className="text-muted-foreground flex flex-col items-center rounded-xl border border-dashed px-4 py-10 text-center text-sm">
          <SearchIcon className="mb-3 size-5" />
          {t.publishedAgents.studio.noMatchingSkills}
        </div>
      ) : null}

      {(["public", "private"] as const).map((source) => {
        const group = filteredSkills.filter((skill) => skill.source === source);
        if (hasQuery && group.length === 0) {
          return null;
        }
        return (
          <section key={source}>
            <div className="mb-3 flex items-center gap-2">
              {source === "public" ? (
                <Globe2Icon className="text-muted-foreground size-4" />
              ) : (
                <LockKeyholeIcon className="text-muted-foreground size-4" />
              )}
              <h3 className="text-sm font-semibold">
                {source === "public"
                  ? t.publishedAgents.studio.publicSkills
                  : t.publishedAgents.studio.privateSkills}
              </h3>
              <Badge variant="secondary" className="font-mono">
                {group.length}
              </Badge>
            </div>

            {group.length === 0 ? (
              <div className="text-muted-foreground rounded-xl border border-dashed px-4 py-8 text-center text-sm">
                {t.publishedAgents.studio.emptySkills}
              </div>
            ) : (
              <div className="grid gap-3 lg:grid-cols-2">
                {group.map((skill) => {
                  const isSelected = selectedNames.has(skill.skill_name);
                  const requirements = skill.declared_connector_caps ?? [];
                  const displayName = getDisplayName(skill);
                  const description = getDescription(skill);
                  const missing = requirements.filter(
                    (capability) => !grantedCapabilities.has(capability),
                  );
                  return (
                    <button
                      key={skill.skill_name}
                      type="button"
                      role="checkbox"
                      aria-checked={isSelected}
                      aria-label={`${displayName} (${skill.skill_name})`}
                      onClick={() => toggle(skill)}
                      className={cn(
                        "group hover:border-foreground/25 focus-visible:ring-ring/50 rounded-xl border p-4 text-left transition-all outline-none focus-visible:ring-3",
                        isSelected
                          ? "border-foreground/35 bg-foreground/[0.035] shadow-sm"
                          : "bg-card",
                      )}
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold">
                            {displayName}
                          </p>
                          {displayName !== skill.skill_name ? (
                            <p className="text-muted-foreground mt-0.5 truncate font-mono text-[11px]">
                              {skill.skill_name}
                            </p>
                          ) : null}
                          <p className="text-muted-foreground mt-1 line-clamp-2 min-h-10 text-xs leading-5">
                            {description ?? "—"}
                          </p>
                        </div>
                        <span
                          className={cn(
                            "flex size-5 shrink-0 items-center justify-center rounded-md border transition-colors",
                            isSelected
                              ? "border-foreground bg-foreground text-background"
                              : "border-input bg-background",
                          )}
                        >
                          {isSelected ? (
                            <CheckIcon className="size-3.5" />
                          ) : null}
                        </span>
                      </div>

                      <div className="mt-4 border-t pt-3">
                        <p className="text-muted-foreground mb-2 flex items-center gap-1.5 text-[10px] font-semibold tracking-wide uppercase">
                          <PlugZapIcon className="size-3" />
                          {t.publishedAgents.studio.connectorRequirements}
                        </p>
                        {requirements.length === 0 ? (
                          <p className="text-muted-foreground text-xs">
                            {t.publishedAgents.studio.noConnectorRequired}
                          </p>
                        ) : (
                          <div className="flex flex-wrap gap-1.5">
                            {requirements.map((capability) => {
                              const isMissing =
                                !grantedCapabilities.has(capability);
                              return (
                                <Badge
                                  key={capability}
                                  variant="outline"
                                  className={cn(
                                    "font-mono font-normal",
                                    isSelected &&
                                      isMissing &&
                                      "border-amber-400 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300",
                                  )}
                                >
                                  {isSelected && isMissing ? (
                                    <CircleAlertIcon />
                                  ) : null}
                                  {capability}
                                </Badge>
                              );
                            })}
                          </div>
                        )}
                        {isSelected && missing.length > 0 ? (
                          <p className="mt-2 flex items-center gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-300">
                            <CircleAlertIcon className="size-3.5" />
                            {t.publishedAgents.studio.missingGrant}
                          </p>
                        ) : isSelected && requirements.length > 0 ? (
                          <p className="mt-2 flex items-center gap-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-300">
                            <CheckIcon className="size-3.5" />
                            {t.publishedAgents.studio.granted}
                          </p>
                        ) : null}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
