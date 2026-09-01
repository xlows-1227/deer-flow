const CODE_PLACEHOLDER = "\u0000CODE_BLOCK\u0000";

/**
 * Preprocess LLM-generated markdown to fix common formatting issues
 * that would otherwise render as a wall of text.
 *
 * Only operates OUTSIDE of fenced code blocks (```...```).
 *
 * Rules:
 *   1. Fix headers missing space:       `###title` → `### title`
 *   2. Fix ordered lists missing space:  `1.item`   → `1. item`
 *   3. Insert blank line before structural markers
 *      (headers, table rows, list items) when the previous line
 *      is plain text — but NOT between consecutive rows of the
 *      same kind (e.g. table rows stay glued together).
 *
 * Safe / low-risk by design:
 *   - Code blocks are extracted first and untouched.
 *   - Rules only match at the START of a line, so URLs, paths,
 *     version numbers (1.2.3) etc. are never touched.
 *   - Consecutive table / list rows are kept consecutive.
 */
export function preprocessMarkdown(raw: string): string {
  if (!raw) return raw;

  // ── Step 1: Protect fenced code blocks ──────────────────────
  const codeBlocks: string[] = [];
  const protectedText = raw.replace(
    /```[\s\S]*?(?:```|$)/g,
    (match) => {
      codeBlocks.push(match);
      return `${CODE_PLACEHOLDER}_${codeBlocks.length - 1}${CODE_PLACEHOLDER}`;
    },
  );

  // ── Step 2: Process line by line ────────────────────────────
  const lines = protectedText.split("\n");
  const result: string[] = [];

  const isCodePlaceholder = (line: string) =>
    new RegExp(`^${CODE_PLACEHOLDER}_\\d+${CODE_PLACEHOLDER}$`).test(line);

  const isHeaderLine = (line: string) => /^#{1,6}\s/.test(line);
  const isTableRow = (line: string) => /^\|/.test(line.trimStart());
  const isListItem = (line: string) =>
    /^[-*+]\s/.test(line.trimStart()) ||
    /^\d+\.\s/.test(line.trimStart());
  const isCodeFenceLine = (line: string) => /^```/.test(line.trimStart());

  for (const rawLine of lines) {
    let line = rawLine;

    // ── 2a / 2b: Fix header & list-item spacing ──────────────
    line = line.replace(/^(#{1,6})([^\s#])/, "$1 $2");
    line = line.replace(/^(\d+\.)([^\s])/, "$1 $2");

    // ── 2c: Ensure blank line before structural markers ───────
    const prevLine = result[result.length - 1] ?? "";
    const prevIsBlank = prevLine.trim() === "";
    const isFirstContentLine = result.length === 0 || prevIsBlank;

    if (!isFirstContentLine) {
      // Determine what came before (skip blank lines in result)
      const prevContentLine =
        [...result].reverse().find((l) => l.trim() !== "") ?? "";

      const thisIsStructural =
        isHeaderLine(line) ||
        isTableRow(line) ||
        isListItem(line) ||
        isCodeFenceLine(line);

      if (thisIsStructural) {
        // Same-kind continuation? → don't insert blank
        const prevWasTable = isTableRow(prevContentLine);
        const prevWasListItem = isListItem(prevContentLine);
        const thisIsTable = isTableRow(line);
        const thisIsListItem = isListItem(line);

        const sameKindContinuation =
          (thisIsTable && prevWasTable) ||
          (thisIsListItem && prevWasListItem);

        if (!sameKindContinuation) {
          result.push("");
        }
      }
    }

    result.push(line);
  }

  // ── Step 3: Restore code blocks ─────────────────────────────
  let processed = result.join("\n");
  processed = processed.replace(
    new RegExp(`${CODE_PLACEHOLDER}_(\\d+)${CODE_PLACEHOLDER}`, "g"),
    (_, index) => codeBlocks[Number(index)] ?? "",
  );

  return processed;
}

export function extractTitleFromMarkdown(markdown: string) {
  if (markdown.startsWith("# ")) {
    let title = markdown.split("\n")[0]!.trim();
    if (title.startsWith("# ")) {
      title = title.slice(2).trim();
    }
    return title;
  }
  return undefined;
}
