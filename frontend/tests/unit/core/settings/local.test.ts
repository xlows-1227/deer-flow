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
  expect(DEFAULT_LOCAL_SETTINGS.context.reasoning_effort).toBe("minimal");
});

test("thread settings do not inherit global chat context", () => {
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

  expect(settings.context).toEqual(DEFAULT_LOCAL_SETTINGS.context);
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
