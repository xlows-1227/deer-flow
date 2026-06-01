import { afterEach, describe, expect, test } from "vitest";

import {
  __resetSlashCommandRegistry,
  filterSlashCommands,
  getSlashCommands,
  registerSlashCommand,
  unregisterSlashCommand,
  type SlashCommand,
} from "@/core/slash-commands";

const sample: SlashCommand[] = [
  {
    id: "skill:researcher",
    name: "researcher",
    kind: "skill",
    value: "researcher",
    label: "Researcher",
    description: "Deep research",
  },
  {
    id: "skill:auto",
    name: "auto",
    aliases: ["none", "off"],
    kind: "skill",
    value: null,
    label: "Auto",
    description: "Let the model pick",
  },
  {
    id: "mode:flash",
    name: "flash",
    kind: "mode",
    value: "flash",
    label: "Flash",
  },
  {
    id: "model:open",
    name: "model",
    aliases: ["llm"],
    kind: "model",
    value: null,
    label: "Model",
  },
  {
    id: "meta:help",
    name: "help",
    aliases: ["?"],
    kind: "help",
    value: null,
    label: "Help",
  },
  {
    id: "team:standup",
    name: "standup",
    kind: "custom",
    value: null,
    label: "Daily standup",
    keywords: ["meeting", "scrum", "daily"],
  },
];

afterEach(() => {
  __resetSlashCommandRegistry();
});

describe("registerSlashCommand", () => {
  test("registers a command and exposes it via getSlashCommands", () => {
    registerSlashCommand(sample[0]!);
    expect(getSlashCommands().map((c) => c.id)).toEqual(["skill:researcher"]);
  });

  test("is idempotent on id — re-registering replaces the existing entry", () => {
    registerSlashCommand(sample[0]!);
    registerSlashCommand({
      ...sample[0]!,
      label: "Researcher v2",
    });
    expect(getSlashCommands()).toHaveLength(1);
    expect(getSlashCommands()[0]?.label).toBe("Researcher v2");
  });

  test("unregisterSlashCommand removes by id", () => {
    registerSlashCommand(sample[0]!);
    registerSlashCommand(sample[1]!);
    unregisterSlashCommand("skill:researcher");
    expect(getSlashCommands().map((c) => c.id)).toEqual(["skill:auto"]);
  });
});

describe("filterSlashCommands", () => {
  test("empty query returns everything", () => {
    sample.forEach((c) => registerSlashCommand(c));
    expect(filterSlashCommands("")).toHaveLength(sample.length);
    expect(filterSlashCommands("   ")).toHaveLength(sample.length);
  });

  test("matches against name case-insensitively", () => {
    sample.forEach((c) => registerSlashCommand(c));
    expect(filterSlashCommands("RESEAR").map((c) => c.id)).toEqual([
      "skill:researcher",
    ]);
  });

  test("matches against aliases", () => {
    sample.forEach((c) => registerSlashCommand(c));
    expect(filterSlashCommands("llm").map((c) => c.id)).toEqual(["model:open"]);
  });

  test("matches against custom keywords", () => {
    sample.forEach((c) => registerSlashCommand(c));
    expect(filterSlashCommands("scrum").map((c) => c.id)).toEqual([
      "team:standup",
    ]);
  });

  test("trims whitespace before matching", () => {
    sample.forEach((c) => registerSlashCommand(c));
    expect(filterSlashCommands("  flash  ").map((c) => c.id)).toEqual([
      "mode:flash",
    ]);
  });

  test("returns empty list when nothing matches", () => {
    sample.forEach((c) => registerSlashCommand(c));
    expect(filterSlashCommands("zzz-nope")).toEqual([]);
  });

  test("accepts an explicit commands array (does not mutate store)", () => {
    const out = filterSlashCommands("re", sample);
    expect(out.map((c) => c.id)).toEqual(["skill:researcher"]);
    // The store should not have been mutated.
    expect(getSlashCommands()).toEqual([]);
  });
});
