// ----------------------------------------------------------------------------
// Built-in slash commands
// ----------------------------------------------------------------------------
//
// Pure factory: given an i18n translation bundle and runtime context
// (current skills / mode), return the list of commands that should
// show up in the picker. The host merges this with whatever third-party
// commands are registered via `registerSlashCommand`.
//
// We split "select skill X" into one command per enabled skill — the
// picker shows them in a flat list, and the user types to filter. We
// could later collapse them into a single `/skill` with a second-level
// picker, but a flat list is simpler and the team has <30 skills.

import type { Skill } from "@/core/skills/type";

import type { SlashCommand } from "./types";

export interface BuiltinCommandContext {
  /** All skills (enabled + disabled). We filter to enabled below. */
  skills: readonly Skill[];
  /**
   * Display labels for the four modes. The chat input owns these strings
   * (they're translated), so we accept them pre-translated.
   */
  modeLabels: {
    flash: string;
    thinking: string;
    pro: string;
    ultra: string;
  };
  /** Localized "Auto" / skill picker title. */
  noSkillLabel: string;
  noSkillDescription: string;
  /** Localized labels for the meta commands. */
  modelLabel: string;
  modelDescription: string;
  clearLabel: string;
  clearDescription: string;
  helpLabel: string;
  helpDescription: string;
}

export function getBuiltinSlashCommands(
  ctx: BuiltinCommandContext,
): SlashCommand[] {
  const commands: SlashCommand[] = [];
  const enabled = ctx.skills.filter((s) => s.enabled);

  // ── Skill commands ─────────────────────────────────────────────────────
  // "Auto" first — clears the active skill.
  commands.push({
    id: "skill:auto",
    name: "auto",
    aliases: ["none", "off"],
    kind: "skill",
    value: null,
    label: ctx.noSkillLabel,
    description: ctx.noSkillDescription,
    keywords: ["skill"],
  });
  // One row per enabled skill. `name` is what the user types; `label`
  // is what we show. Search by either.
  for (const skill of enabled) {
    commands.push({
      id: `skill:${skill.name}`,
      name: skill.name,
      kind: "skill",
      value: skill.name,
      label: skill.display_name ?? skill.name,
      description: skill.description,
      keywords: ["skill"],
    });
  }

  // ── Mode commands ──────────────────────────────────────────────────────
  const modes: Array<{
    id: "flash" | "thinking" | "pro" | "ultra";
    label: string;
  }> = [
    { id: "flash", label: ctx.modeLabels.flash },
    { id: "thinking", label: ctx.modeLabels.thinking },
    { id: "pro", label: ctx.modeLabels.pro },
    { id: "ultra", label: ctx.modeLabels.ultra },
  ];
  for (const m of modes) {
    commands.push({
      id: `mode:${m.id}`,
      name: m.id,
      kind: "mode",
      value: m.id,
      label: m.label,
      keywords: ["mode"],
    });
  }

  // ── Model command ─────────────────────────────────────────────────────
  commands.push({
    id: "model:open",
    name: "model",
    aliases: ["models", "llm"],
    kind: "model",
    value: null,
    label: ctx.modelLabel,
    description: ctx.modelDescription,
    keywords: ["model", "llm"],
  });

  // ── Meta commands ─────────────────────────────────────────────────────
  commands.push({
    id: "meta:clear",
    name: "clear",
    aliases: ["reset"],
    kind: "clear",
    value: null,
    label: ctx.clearLabel,
    description: ctx.clearDescription,
    keywords: ["meta"],
  });
  commands.push({
    id: "meta:help",
    name: "help",
    aliases: ["?", "commands"],
    kind: "help",
    value: null,
    label: ctx.helpLabel,
    description: ctx.helpDescription,
    keywords: ["meta"],
  });

  return commands;
}
