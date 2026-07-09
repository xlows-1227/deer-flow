import { expect, test } from "vitest";

import {
  DEFAULT_LOCAL_SETTINGS,
  applyThreadContextOverride,
} from "@/core/settings/local";

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
