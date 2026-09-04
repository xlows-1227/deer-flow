const CODE_PLACEHOLDER = "\u0000CODE_BLOCK\u0000";

/**
 * LangGraph raw tool-call protocol markers.  These are NOT markdown —
 * they're internal tags that the backend leaks into the AI message body
 * when a stream fails mid-tool-call.
 *
 * Two real-world tag formats observed:
 *   1. XML-ish:  <tool_call_section_begin>...</tool_call_end>
 *   2. Pipe-ish: <|tool_calls_section_begin|> ... <|tool_call_end|>
 */
const TOOL_CALL_OPEN_TAG =
  "(?:<tool_call[^>]*>|<\\|tool_call[^|]*\\|>|<function_calls>|<\\|function_calls\\|>)";
const TOOL_CALL_CLOSE_TAG =
  "(?:<\\/tool_call[^>]*>|<\\/function_calls>|<\\|tool_call[^|]*end\\|>|<\\|tool_call[^|]*_end\\|>)";

const LANGGRAPH_TOOL_CALL_BLOCK_REGEX = new RegExp(
  `${TOOL_CALL_OPEN_TAG}[\\s\\S]*?(?:${TOOL_CALL_CLOSE_TAG}|$)`,
  "g",
);
const LANGGRAPH_STRAY_TAG_REGEX = new RegExp(
  `<\\/?tool_call[^>]*>|<\\/?function_calls>|<\\|[^|]*tool_call[^|]*\\|>|<\\|[^|]*function_calls\\|>`,
  "g",
);

/**
 * Preprocess LLM-generated markdown to fix common formatting issues
 * that would otherwise render as a wall of text.
 *
 * ONLY operates OUTSIDE of fenced code blocks.
 *
 * Pipeline:
 *   Pre-Step — Strip LangGraph tool-call tags (XML and pipe formats).
 *   Step 0   — Line-break recovery for "one long line" input.
 *              VERY conservative — only touches markers where we can
 *              be 99% certain they need a newline.  Specifically:
 *                0a  ---##Title       → \n---\n## Title
 *                0b  word##Title      → word\n## Title
 *                0c  |---|---| 表格分隔行 → 前面加 \n
 *                0d  |col|col| 表格内容行 → 前面加 \n
 *              NOTE: We deliberately do NOT try to recover list markers
 *              (- item, * item, 1. item) because `-建议` / `-少油` in
 *              CJK text is indistinguishable from bullet items.
 *   Step 1   — Extract + protect fenced code blocks.
 *   Step 2   — Fix header spacing + blank-line separation between
 *              structural markers.
 *   Step 3   — Restore code blocks.
 */
/**
 * Count table cells in a markdown table row line.
 *
 *   |a|b|c|    → 3 cells (4 pipes − 1)
 *   ||a|b|     → 3 cells (first cell empty, 4 pipes − 1)
 *   |a|        → 1 cell  (2 pipes − 1)
 *
 * We deliberately ignore escaped pipes (`\|`) — LLM table output
 * rarely uses them and accounting for them would complicate the heuristic.
 */
function countTableCells(line: string): number {
  const trimmed = line.trim();
  if (!trimmed.startsWith("|")) return 0;
  let pipeCount = 0;
  for (let i = 0; i < trimmed.length; i++) {
    if (trimmed[i] === "|") pipeCount += 1;
  }
  // |a|b|c| has 4 pipes → 3 cells.  Minimum 1 pipe means no cells.
  return Math.max(0, pipeCount - 1);
}

function isTableSeparatorLine(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.startsWith("|")) return false;
  // |---|---|  or  |:---:|:---:|  or  | --- | --- |
  const cells = trimmed.split("|").slice(1, -1);
  if (cells.length === 0) return false;
  return cells.every((c) => /^[\s\-:]+$/.test(c));
}

/**
 * Join table rows that the LLM split across multiple lines.
 *
 * The LLM frequently emits a 4-column data row as two lines:
 *
 *   | GUID |
 *   | varchar | NO | 主键 |
 *
 * The markdown renderer treats each line as its own row, which makes
 * the second row's cells shift left into the wrong columns.  We detect
 * this by comparing each row's cell count to the table's expected
 * column count (taken from the separator row), and merging consecutive
 * rows until the cumulative cell count reaches the expected count.
 *
 * This is conservative: it only fires when a row has STRICTLY FEWER
 * cells than the separator.  A row that already matches the expected
 * count is emitted as-is.
 */
function fixBrokenTableRowsByCellCount(text: string): string {
  const lines = text.split("\n");
  const result: string[] = [];

  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    // A table block is a run of consecutive lines that start with `|`.
    if (line.trimStart().startsWith("|")) {
      const block: string[] = [];
      while (i < lines.length && lines[i]!.trimStart().startsWith("|")) {
        block.push(lines[i]!);
        i += 1;
      }
      result.push(...repairTableCellCount(block));
    } else {
      result.push(line);
      i += 1;
    }
  }
  return result.join("\n");
}

function repairTableCellCount(block: string[]): string[] {
  if (block.length < 3) return block;

  // Pre-merge split separator lines: the LLM sometimes splits the separator
  // row (|---|---|---|) across two or more lines:
  //   |---
  //   |---|---|
  // or:
  //   |---|---
  //   |---|
  //
  // A partial separator line starts with `|` and contains ONLY dashes,
  // colons, and whitespace (no content cells).  We merge consecutive
  // partial separators into one line before doing cell-count repair.
  const mergedBlock: string[] = [];
  for (let i = 0; i < block.length; i++) {
    const line = block[i]!;
    const trimmed = line.trim();
    if (trimmed.startsWith("|") && /^[|\s\-:]+$/.test(trimmed)) {
      let merged = trimmed;
      while (i + 1 < block.length) {
        const nextTrimmed = block[i + 1]!.trim();
        if (nextTrimmed.startsWith("|") && /^[|\s\-:]+$/.test(nextTrimmed)) {
          merged = merged + nextTrimmed;
          i += 1;
        } else {
          break;
        }
      }
      mergedBlock.push(merged);
    } else {
      mergedBlock.push(line);
    }
  }

  // Find the separator row (typically the 2nd line, but be defensive).
  let separatorIdx = -1;
  for (let i = 0; i < Math.min(mergedBlock.length, 4); i++) {
    if (isTableSeparatorLine(mergedBlock[i]!)) {
      separatorIdx = i;
      break;
    }
  }
  if (separatorIdx === -1) return mergedBlock;

  const expectedCells = countTableCells(mergedBlock[separatorIdx]!);
  if (expectedCells < 2) return mergedBlock;

  const out: string[] = [];
  // Lines before the separator (header) — kept as-is, but also repaired
  // in case the header itself is split across two lines.
  const headerLines = mergedBlock.slice(0, separatorIdx);
  out.push(...mergeRowsToCellCount(headerLines, expectedCells));
  out.push(mergedBlock[separatorIdx]!);

  // Data rows after the separator — these are the most common split case.
  const dataLines = mergedBlock.slice(separatorIdx + 1);
  out.push(...mergeRowsToCellCount(dataLines, expectedCells));
  return out;
}

/**
 * Walk through `rows` and merge consecutive rows until the merged row's
 * cell count reaches `expectedCells`.  Once it does, emit the merged row
 * and start a new one.  A single row that already has ≥ expectedCells is
 * emitted untouched.
 *
 * Merging two rows strips the trailing `|` of the first row and the
 * leading `|` of the next, then joins them with ` | ` so the boundary
 * becomes a normal cell separator:
 *
 *   | GUID |   +   | varchar | NO | 主键 |   →   | GUID | varchar | NO | 主键 |
 */
function mergeRowsToCellCount(
  rows: string[],
  expectedCells: number,
): string[] {
  if (rows.length === 0) return [];
  const out: string[] = [];
  let current: string | null = null;
  let currentCells = 0;

  for (const row of rows) {
    const cells = countTableCells(row);
    if (current === null) {
      current = row;
      currentCells = cells;
    } else {
      // Strip trailing `|` (with surrounding whitespace) from current,
      // strip leading `|` (with surrounding whitespace) from `row`,
      // then glue with " | " as the new cell boundary.
      const head = current.replace(/\s*\|\s*$/, "");
      const tail = row.replace(/^\s*\|\s*/, "");
      current = `${head} | ${tail}`;
      currentCells += cells;
    }
    if (currentCells >= expectedCells) {
      out.push(current!);
      current = null;
      currentCells = 0;
    }
  }
  if (current !== null) out.push(current);
  return out;
}

export function preprocessMarkdown(raw: string): string {
  if (!raw) return raw;

  // ── Pre-Step: Strip LangGraph tool-call blocks + stray tags ────
  let text = raw
    .replace(LANGGRAPH_TOOL_CALL_BLOCK_REGEX, "")
    .replace(LANGGRAPH_STRAY_TAG_REGEX, "");
  // Also strip <!--DF_RAW_ERROR:...--> HTML comment (belt-and-suspenders).
  text = text.replace(/\n?<!--DF_RAW_ERROR:[\s\S]*?-->\s*$/, "");

  // ── Step 0: Line-break recovery ─────────────────────────────
  // These fixes apply regardless of line count, because LLM output
  // often has inline ## headers and table rows even in shorter content.
  {
    const insideBold = (s: string) => {
      const count = (s.match(/\*\*/g) ?? []).length;
      return count % 2 === 1;
    };

    // 0a. Chapter separator pattern: ---##Title  →  \n---\n## Title
    text = text.replace(/(---)(#{1,6})(?=\S)/g, "\n$1\n$2 ");

    // 0b. Inline headers: word##Title  →  word\n## Title
    text = text.replace(/(?<=[^\s\n-])(#{1,6})(?=[^\s#])/g, "\n$1 ");

    // 0c. Table separator row: |---|---|  → ensure newline before it
    //     SKIP if we're inside ** block (would split bold marker).
    text = text.replace(
      /([^\n])(\|[\s\-:]{3,}(?:\|[\s\-:]{3,})+\|)/g,
      (_match, prefix: string, row: string) => {
        if (insideBold(prefix)) return prefix + row; // don't break bold!
        return prefix + "\n" + row;
      },
    );

    // 0d. Table content rows: |col|col|  → ensure newline before it
    //     ALSO skip inside ** block.
    text = text.replace(
      /([^\n|])(\|(?:[^\n|]+\|){2,})/g,
      (_match, prefix: string, row: string) => {
        // Skip if inside bold block
        if (insideBold(prefix)) return prefix + row;
        // Check if this row is actually a separator row — skip (0c handles it)
        const cells = row.split("|").filter((c) => c.length > 0);
        const isSeparatorRow = cells.every((c) => /^[\s\-:]+$/.test(c));
        if (isSeparatorRow) return prefix + row;
        return prefix + "\n" + row;
      },
    );

    // 0e. Split glued table rows: |cell1|cell2||cell3|cell4|  →  |cell1|cell2|\n|cell3|cell4|
    //     Step 0d can't handle this because its prefix excludes `|`,
    //     so a data row glued directly after a separator/another row
    //     (ending with `|`) never gets a newline.
    //
    //     IMPORTANT: We only split `||` when the row BEFORE it has at
    //     least 2 cells (i.e. `|cell1|cell2|`).  This distinguishes a
    //     complete row boundary from an empty cell (`|cell1||cell2|`
    //     has only 1 cell before `||`, so it's an empty cell, not a
    //     row boundary).  Splitting an empty cell would break the
    //     table by turning one row into two misaligned rows.
    text = text.replace(
      /(\|(?:[^\n|]+\|){2,})\|(?=[^\s\n|])/g,
      "$1\n|",
    );

    // 0f. Join broken table rows: the LLM sometimes splits a single
    //     table row across two lines, where the second line starts
    //     with an empty cell (|| or | |).  This causes the markdown
    //     renderer to create two misaligned rows instead of one.
    //
    //     | GUID |\n|| varchar | NO | 主键 |  →  | GUID | varchar | NO | 主键 |
    //
    //     We detect: a line ending with `|` followed by a newline
    //     and a line starting with `||` or `| |` (empty first cell).
    //     The join removes the boundary `|` + `\n` + `||`, replacing
    //     with a single `|` as the cell separator.
    //     This does NOT match normal two-row tables because a normal
    //     second row starts with `| cell` (pipe-space-content), not
    //     `||` (pipe-pipe).
    text = text.replace(
      /(\|)\s*\n\s*\|\s*\|/g,
      "$1",
    );

    // 0g. Join broken table rows by cell count: the LLM also splits a
    //     single row across two lines where the second line starts
    //     with a NORMAL cell (not empty).  Step 0f can't catch this
    //     because the second line starts with `| varchar` (not `||`).
    //
    //     | GUID |\n| varchar | NO | 主键 |  →  | GUID | varchar | NO | 主键 |
    //
    //     Strategy: walk consecutive table-row lines as a block, find
    //     the separator row to learn the expected column count, then
    //     merge any run of rows whose cumulative cell count is below
    //     the expected count.  Once a merged row reaches the expected
    //     count, emit it and start a new row.
    text = fixBrokenTableRowsByCellCount(text);
  }

  // ── Step 1: Protect fenced code blocks ──────────────────────
  const codeBlocks: string[] = [];
  let protectedText = text.replace(
    /```[\s\S]*?(?:```|$)/g,
    (match) => {
      codeBlocks.push(match);
      return `${CODE_PLACEHOLDER}_${codeBlocks.length - 1}${CODE_PLACEHOLDER}`;
    },
  );

  // ── Step 1b: Balance unbalanced ** bold markers ─────────────
  // Old/historical messages sometimes have an odd number of ** markers
  // (e.g. a missing closing **), which causes the markdown renderer to
  // treat everything after the last unmatched ** as bold — "all text is
  // bold".  If the total count is odd, append a closing ** at the end.
  {
    const boldCount = (protectedText.match(/\*\*/g) ?? []).length;
    if (boldCount % 2 === 1) {
      protectedText = protectedText + "**";
    }
  }

  // ── Step 2: Fix spacing + blank lines ────────────────────────
  const lines = protectedText.split("\n");
  const result: string[] = [];

  const isHeaderLine = (line: string) => /^#{1,6}\s/.test(line);
  const isSeparatorLine = (line: string) => /^-{3,}\s*$/.test(line.trim());
  const isTableRow = (line: string) => /^\|/.test(line.trimStart());
  const isListItem = (line: string) =>
    /^[-*+]\s/.test(line.trimStart()) ||
    /^\d+\.\s/.test(line.trimStart());
  const isCodeFenceLine = (line: string) => /^```/.test(line.trimStart());

  for (const rawLine of lines) {
    let line = rawLine;

    // 2a/2b: Fix header spacing (`###title` → `### title`)
    line = line.replace(/^(#{1,6})([^\s#])/, "$1 $2");
    line = line.replace(/^(\d+\.)([^\s])/, "$1 $2");

    // 2c: Fix bare list markers — LLM often outputs `-内容` instead of `- 内容`.
    //     Only fix when: 1) line truly starts with list marker (after trim),
    //                    2) marker char is NOT `*` (it conflicts with italic),
    //                    3) marker is NOT followed by another `-` or digit.
    //     CJK connector hyphens (`-少油少盐`) on NON-start-of-line are fine.
    const trimmedStart = line.trimStart();
    if (/^[-+]\S/.test(trimmedStart) && !/^[-+][-\d]/.test(trimmedStart)) {
      line = line.replace(/^([ \t]*)([-+])(\S)/, "$1$2 $3");
    }

    // 2c: Ensure blank line BEFORE structural markers
    //     But NOT before: horizontal rule --- (it can follow text)
    //                     separator row |---| (continuation of table)
    const prevLine = result[result.length - 1] ?? "";
    const prevIsBlank = prevLine.trim() === "";
    if (!prevIsBlank && result.length > 0) {
      const prevContentLine =
        [...result].reverse().find((l) => l.trim() !== "") ?? "";

      const thisIsStructural =
        (isHeaderLine(line) && !isSeparatorLine(line)) ||
        (isTableRow(line) && !/^\|[\s\-:]+/.test(line.trimStart())) || // skip separator rows
        isCodeFenceLine(line);

      if (thisIsStructural) {
        const prevWasTable = isTableRow(prevContentLine);
        const thisIsTable = isTableRow(line);

        const sameKind = thisIsTable && prevWasTable;
        // Also keep consecutive items glued — but list items are rare
        // in real LLM output without Step 0 doing its job, so we're
        // mainly concerned about tables here.

        if (!sameKind) {
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

  // ── Step 4: Collapse 3+ consecutive newlines into 2 ─────────
  // LLM output often has excessive blank lines; this keeps at most
  // one blank line between content blocks.
  processed = processed.replace(/\n{3,}/g, "\n\n");

  return processed.trim();
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
