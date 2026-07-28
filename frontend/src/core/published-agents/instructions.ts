export const SOUL_PRESET_IDS = [
  "professional",
  "warm",
  "concise",
  "coach",
] as const;

export type SoulPresetId = (typeof SOUL_PRESET_IDS)[number];
export type SoulPresetContentMap = Record<SoulPresetId, string>;

export const DEFAULT_SOUL_PRESET_ID: SoulPresetId = "professional";

const SOUL_PRESET_MARKER =
  /^<!--\s*deer-flow:soul-preset:(professional|warm|concise|coach):v1\s*-->/;

interface PrepareAgentInstructionsInput {
  agentMarkdown: string;
  soulMarkdown: string;
  defaultAgentTemplate: string;
  soulPresetContents: SoulPresetContentMap;
}

interface PreparedAgentInstructions {
  agentMarkdown: string;
  soulMarkdown: string;
  selectedSoulPresetId: SoulPresetId | null;
  hasLegacyCustomSoul: boolean;
  modified: boolean;
}

export function createSoulPresetMarkdown(
  presetId: SoulPresetId,
  content: string,
): string {
  return `<!-- deer-flow:soul-preset:${presetId}:v1 -->\n${content.trim()}`;
}

export function detectSoulPresetId(
  soulMarkdown: string,
  soulPresetContents?: SoulPresetContentMap,
): SoulPresetId | null {
  const markerMatch = SOUL_PRESET_MARKER.exec(soulMarkdown.trimStart());
  if (markerMatch) {
    return markerMatch[1] as SoulPresetId;
  }

  if (soulPresetContents) {
    const normalizedSoul = soulMarkdown.trim();
    const matchingPreset = SOUL_PRESET_IDS.find(
      (presetId) => soulPresetContents[presetId].trim() === normalizedSoul,
    );
    return matchingPreset ?? null;
  }

  return null;
}

/**
 * Initialize the two authoring documents without conflating their purposes.
 *
 * AGENT.md remains the user's editable work contract. SOUL.md is generated
 * from a managed preset. Existing custom SOUL.md content is deliberately
 * preserved until the user explicitly replaces it with a preset.
 */
export function prepareAgentInstructions({
  agentMarkdown,
  soulMarkdown,
  defaultAgentTemplate,
  soulPresetContents,
}: PrepareAgentInstructionsInput): PreparedAgentInstructions {
  const preparedAgent = agentMarkdown.trim()
    ? agentMarkdown
    : defaultAgentTemplate;

  if (!soulMarkdown.trim()) {
    const preparedSoul = createSoulPresetMarkdown(
      DEFAULT_SOUL_PRESET_ID,
      soulPresetContents[DEFAULT_SOUL_PRESET_ID],
    );
    return {
      agentMarkdown: preparedAgent,
      soulMarkdown: preparedSoul,
      selectedSoulPresetId: DEFAULT_SOUL_PRESET_ID,
      hasLegacyCustomSoul: false,
      modified:
        preparedAgent !== agentMarkdown || preparedSoul !== soulMarkdown,
    };
  }

  const selectedSoulPresetId = detectSoulPresetId(
    soulMarkdown,
    soulPresetContents,
  );
  return {
    agentMarkdown: preparedAgent,
    soulMarkdown,
    selectedSoulPresetId,
    hasLegacyCustomSoul: selectedSoulPresetId === null,
    modified: preparedAgent !== agentMarkdown,
  };
}
