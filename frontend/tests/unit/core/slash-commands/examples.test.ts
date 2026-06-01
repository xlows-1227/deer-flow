import { afterEach, describe, expect, test, vi } from "vitest";

import {
  __resetSlashCommandRegistry,
  filterSlashCommands,
} from "@/core/slash-commands";
import {
  buildClearDemoCommand,
  buildStandupDemoCommand,
  registerClearDemoCommand,
  registerExampleCommands,
  registerStandupDemoCommand,
} from "@/core/slash-commands/examples";

afterEach(() => {
  __resetSlashCommandRegistry();
  vi.restoreAllMocks();
});

describe("buildClearDemoCommand", () => {
  test("exposes the documented shape", () => {
    const c = buildClearDemoCommand();
    expect(c.id).toBe("demo:clear");
    expect(c.name).toBe("demo-clear");
    expect(c.kind).toBe("custom");
    expect(c.aliases).toEqual(["dclear", "wipe-demo"]);
    expect(c.keywords).toContain("tutorial");
  });

  test("run() returns undefined so the host clears the input", () => {
    const c = buildClearDemoCommand();
    expect(c.run?.("")).toBeUndefined();
    expect(c.run?.("ignored suffix")).toBeUndefined();
  });

  test("run() logs the query when one is supplied", () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => undefined);
    const c = buildClearDemoCommand();
    void c.run?.("hello world");
    expect(info).toHaveBeenCalledTimes(1);
    expect(info).toHaveBeenCalledWith(
      "[demo:clear] run() was called with query:",
      '"hello world"',
    );
  });

  test("run() does NOT log when the query is empty", () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => undefined);
    const c = buildClearDemoCommand();
    void c.run?.("");
    expect(info).not.toHaveBeenCalled();
  });
});

describe("buildStandupDemoCommand", () => {
  test("exposes the documented shape", () => {
    const c = buildStandupDemoCommand();
    expect(c.id).toBe("demo:standup");
    expect(c.name).toBe("standup");
    expect(c.aliases).toEqual(["daily", "scrum"]);
    expect(c.kind).toBe("custom");
  });

  test("run() returns a standup template addressed to the named teammate", () => {
    const c = buildStandupDemoCommand();
    const out = c.run?.("alice") ?? "";
    expect(out).toMatch(/^# Standup — \d{4}-\d{2}-\d{2}/);
    expect(out).toContain("**For:** alice");
    expect(out).toContain("## Yesterday");
    expect(out).toContain("## Today");
    expect(out).toContain("## Blockers");
  });

  test("run() falls back to 'the team' when no name is given", () => {
    const c = buildStandupDemoCommand();
    const out = c.run?.("") ?? "";
    expect(out).toContain("**For:** the team");
  });

  test("run() captures extra context after the name", () => {
    const c = buildStandupDemoCommand();
    const out = c.run?.("alice sprint-42 retro") ?? "";
    expect(out).toContain("**For:** alice (sprint-42 retro)");
  });
});

describe("registerExampleCommands", () => {
  test("registers both demos", () => {
    registerExampleCommands();
    const all = filterSlashCommands("");
    const ids = all.map((c) => c.id);
    expect(ids).toContain("demo:clear");
    expect(ids).toContain("demo:standup");
  });

  test("registerClearDemoCommand is idempotent on id", () => {
    registerClearDemoCommand();
    registerClearDemoCommand();
    const matches = filterSlashCommands("demo-clear");
    expect(matches.filter((c) => c.id === "demo:clear")).toHaveLength(1);
  });

  test("registerStandupDemoCommand is idempotent on id", () => {
    registerStandupDemoCommand();
    registerStandupDemoCommand();
    const matches = filterSlashCommands("standup");
    expect(matches.filter((c) => c.id === "demo:standup")).toHaveLength(1);
  });
});
