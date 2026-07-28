import { describe, expect, test } from "vitest";

import {
  createSoulPresetMarkdown,
  prepareAgentInstructions,
  type SoulPresetContentMap,
} from "@/core/published-agents/instructions";

const template =
  "# Role & goal\nDescribe the role.\n\n# Responsibilities\nDescribe the work.";
const soulPresets: SoulPresetContentMap = {
  professional: "# Professional\nBe rigorous and dependable.",
  warm: "# Warm\nBe patient and encouraging.",
  concise: "# Concise\nLead with the answer.",
  coach: "# Coach\nGuide the user with questions.",
};

describe("prepareAgentInstructions", () => {
  test("prefills an empty draft with the Agent template and default Soul preset", () => {
    expect(
      prepareAgentInstructions({
        agentMarkdown: "",
        soulMarkdown: "",
        defaultAgentTemplate: template,
        soulPresetContents: soulPresets,
      }),
    ).toEqual({
      agentMarkdown: template,
      soulMarkdown: createSoulPresetMarkdown(
        "professional",
        soulPresets.professional,
      ),
      selectedSoulPresetId: "professional",
      hasLegacyCustomSoul: false,
      modified: true,
    });
  });

  test("keeps an existing marked Soul preset unchanged", () => {
    const soulMarkdown = createSoulPresetMarkdown("warm", soulPresets.warm);

    expect(
      prepareAgentInstructions({
        agentMarkdown: "# Existing rules",
        soulMarkdown,
        defaultAgentTemplate: template,
        soulPresetContents: soulPresets,
      }),
    ).toEqual({
      agentMarkdown: "# Existing rules",
      soulMarkdown,
      selectedSoulPresetId: "warm",
      hasLegacyCustomSoul: false,
      modified: false,
    });
  });

  test("recognizes unmarked preset content without rewriting it", () => {
    expect(
      prepareAgentInstructions({
        agentMarkdown: "# Existing rules",
        soulMarkdown: `\n${soulPresets.concise}\n`,
        defaultAgentTemplate: template,
        soulPresetContents: soulPresets,
      }),
    ).toEqual({
      agentMarkdown: "# Existing rules",
      soulMarkdown: `\n${soulPresets.concise}\n`,
      selectedSoulPresetId: "concise",
      hasLegacyCustomSoul: false,
      modified: false,
    });
  });

  test("preserves a legacy custom Soul until the user selects a preset", () => {
    expect(
      prepareAgentInstructions({
        agentMarkdown: "# Existing rules",
        soulMarkdown: "Be precise and warm.",
        defaultAgentTemplate: template,
        soulPresetContents: soulPresets,
      }),
    ).toEqual({
      agentMarkdown: "# Existing rules",
      soulMarkdown: "Be precise and warm.",
      selectedSoulPresetId: null,
      hasLegacyCustomSoul: true,
      modified: false,
    });
  });
});
