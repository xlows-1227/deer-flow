import { afterEach, expect, test, vi } from "vitest";

import {
  DEFAULT_LOCAL_SETTINGS,
  LOCAL_SETTINGS_KEY,
  applyThreadContextOverride,
  getLocalSettings,
} from "@/core/settings/local";

afterEach(() => {
  vi.unstubAllGlobals();
});

function installStorage(settings: unknown) {
  vi.stubGlobal("window", {});
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) =>
      key === LOCAL_SETTINGS_KEY ? JSON.stringify(settings) : null,
    ),
  });
}

test("defaults token usage to header total plus per-turn breakdown", () => {
  expect(DEFAULT_LOCAL_SETTINGS.tokenUsage).toEqual({
    headerTotal: true,
    inlineMode: "per_turn",
  });
});

test("defaults chat mode to flash", () => {
  expect(DEFAULT_LOCAL_SETTINGS.context.mode).toBe("flash");
  // No default reasoning_effort: per-mode defaults apply at submit time and
  // an explicit value would permanently override them.
  expect(DEFAULT_LOCAL_SETTINGS.context.reasoning_effort).toBeUndefined();
});

test("does not promote the legacy per-message effort to a global override", () => {
  installStorage({
    context: {
      model_name: "legacy-model",
      mode: "pro",
      reasoning_effort: "minimal",
    },
  });

  const settings = getLocalSettings();

  expect(settings.context).toMatchObject({
    model_name: "legacy-model",
    mode: "pro",
  });
  expect(settings.version).toBe(DEFAULT_LOCAL_SETTINGS.version);
  expect(settings.context.reasoning_effort).toBeUndefined();
});

test("retains a versioned global reasoning-effort override", () => {
  installStorage({
    version: DEFAULT_LOCAL_SETTINGS.version,
    context: {
      reasoning_effort: "minimal",
    },
  });

  expect(getLocalSettings().context.reasoning_effort).toBe("minimal");
});

test("thread settings do not inherit global chat context except reasoning effort", () => {
  const settings = applyThreadContextOverride(
    {
      ...DEFAULT_LOCAL_SETTINGS,
      context: {
        ...DEFAULT_LOCAL_SETTINGS.context,
        model_name: "last-model",
        mode: "ultra",
        reasoning_effort: "high",
        skill_name: "last-skill",
      },
    },
    undefined,
  );

  // model/mode/skill are per-thread and reset to defaults, but the
  // reasoning-effort override is a global advanced setting that applies
  // to every thread.
  expect(settings.context).toEqual({
    ...DEFAULT_LOCAL_SETTINGS.context,
    reasoning_effort: "high",
  });
});

test("thread settings restore only the selected thread context", () => {
  const settings = applyThreadContextOverride(DEFAULT_LOCAL_SETTINGS, {
    model_name: "thread-model",
    mode: "thinking",
    reasoning_effort: "low",
    skill_name: "thread-skill",
  });

  expect(settings.context).toEqual({
    ...DEFAULT_LOCAL_SETTINGS.context,
    model_name: "thread-model",
    mode: "thinking",
    reasoning_effort: "low",
    skill_name: "thread-skill",
  });
});
