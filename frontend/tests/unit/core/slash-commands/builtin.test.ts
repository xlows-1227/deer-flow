import { describe, expect, test } from "vitest";

import type { Skill } from "@/core/skills/type";
import {
  getBuiltinSlashCommands,
  type BuiltinCommandContext,
} from "@/core/slash-commands";

const skills: Skill[] = [
  {
    name: "researcher",
    display_name: "Researcher",
    description: "Deep research.",
    description_zh: null,
    category: "research",
    license: "MIT",
    enabled: true,
  },
  {
    name: "coder",
    display_name: "Coder",
    description: "",
    description_zh: null,
    category: "engineering",
    license: "MIT",
    enabled: true,
  },
  {
    name: "writer",
    display_name: "Writer",
    description: "",
    description_zh: null,
    category: "writing",
    license: "MIT",
    enabled: false,
  },
];

const ctx: BuiltinCommandContext = {
  skills,
  modeLabels: {
    flash: "Flash",
    thinking: "Reasoning",
    pro: "Pro",
    ultra: "Ultra",
  },
  noSkillLabel: "Auto",
  noSkillDescription: "Let the model pick",
  modelLabel: "Model",
  modelDescription: "Switch the chat model",
  clearLabel: "Clear input",
  clearDescription: "Empty the message",
  helpLabel: "Help",
  helpDescription: "Show all slash commands",
};

describe("getBuiltinSlashCommands", () => {
  test("includes exactly one Auto skill command", () => {
    const out = getBuiltinSlashCommands(ctx);
    const autos = out.filter((c) => c.kind === "skill" && c.value === null);
    expect(autos).toHaveLength(1);
    expect(autos[0]?.name).toBe("auto");
  });

  test("emits one command per enabled skill, skipping disabled ones", () => {
    const out = getBuiltinSlashCommands(ctx);
    const skillsOut = out.filter((c) => c.kind === "skill" && c.value !== null);
    expect(skillsOut.map((c) => c.name).sort()).toEqual([
      "coder",
      "researcher",
    ]);
  });

  test("emits one command per mode", () => {
    const out = getBuiltinSlashCommands(ctx);
    const modes = out.filter((c) => c.kind === "mode");
    expect(modes.map((c) => c.name).sort()).toEqual([
      "flash",
      "pro",
      "thinking",
      "ultra",
    ]);
  });

  test("emits model / clear / help commands", () => {
    const out = getBuiltinSlashCommands(ctx);
    const kinds = new Set(out.map((c) => c.kind));
    expect(kinds.has("model")).toBe(true);
    expect(kinds.has("clear")).toBe(true);
    expect(kinds.has("help")).toBe(true);
  });

  test("each enabled skill command has a value matching the skill name", () => {
    const out = getBuiltinSlashCommands(ctx);
    for (const skill of skills.filter((s) => s.enabled)) {
      const cmd = out.find((c) => c.id === `skill:${skill.name}`);
      expect(cmd?.value).toBe(skill.name);
    }
  });
});
