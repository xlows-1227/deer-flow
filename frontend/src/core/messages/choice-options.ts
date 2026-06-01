export type MessageChoiceOption = {
  index: number;
  value: string;
};

export type MessageChoiceOptions = {
  prompt: string;
  options: MessageChoiceOption[];
};

const OPTION_LINE_RE = /^\s*(\d{1,2})[\.)、．]\s+(.+?)\s*$/;
const MAX_OPTIONS = 8;

function normalizeOptionValue(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

export function extractMessageChoiceOptions(
  content: string | null | undefined,
): MessageChoiceOptions | null {
  const text = content?.trim();
  if (!text) {
    return null;
  }

  const lines = text.split(/\r?\n/);
  let end = lines.length - 1;
  while (end >= 0 && !lines[end]?.trim()) {
    end -= 1;
  }

  const optionLines: Array<{
    lineIndex: number;
    optionIndex: number;
    value: string;
  }> = [];

  for (let lineIndex = end; lineIndex >= 0; lineIndex -= 1) {
    const line = lines[lineIndex];
    if (!line) {
      break;
    }

    const match = OPTION_LINE_RE.exec(line);
    if (!match) {
      break;
    }

    const optionIndex = Number(match[1]);
    const value = normalizeOptionValue(match[2] ?? "");
    if (!Number.isInteger(optionIndex) || !value) {
      break;
    }

    optionLines.unshift({ lineIndex, optionIndex, value });
  }

  if (optionLines.length < 2 || optionLines.length > MAX_OPTIONS) {
    return null;
  }

  const startsAtOne = optionLines[0]?.optionIndex === 1;
  const isSequential = optionLines.every(
    (option, index) => option.optionIndex === index + 1,
  );
  if (!startsAtOne || !isSequential) {
    return null;
  }

  const firstOptionLine = optionLines[0]!.lineIndex;
  const prompt = lines.slice(0, firstOptionLine).join("\n").trim();
  if (!prompt) {
    return null;
  }

  return {
    prompt,
    options: optionLines.map((option) => ({
      index: option.optionIndex,
      value: option.value,
    })),
  };
}
