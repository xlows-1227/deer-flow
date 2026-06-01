// ----------------------------------------------------------------------------
// Slash-picker parsing helpers
// ----------------------------------------------------------------------------
//
// Pure functions used by the chat input's `/` picker. The picker itself
// is command-agnostic — it shows a list of `SlashCommand`s and lets the
// user pick one. The detection / wrapping helpers below live here because
// they're tied to *typing* a slash command, not to any specific command.

export interface SlashPickerMatch {
  /** Whether the input currently has an active slash command. */
  active: boolean;
  /** The query after the leading `/` (e.g. `"re"` for `/re`). */
  query: string;
  /**
   * 0-based start offset of the slash token in the input. Useful for
   * restoring the caret position when the menu closes.
   */
  start: number;
}

/**
 * Detect whether `value` ends with a slash command. A slash command is a
 * token that:
 *   - starts with `/`,
 *   - contains no whitespace,
 *   - is at the end of the input (i.e. the user is still typing it).
 *
 * The token is allowed to be just `/` (empty query), and we don't trigger
 * on `/` that appears in the middle of other text — only at the active
 * caret position. Because the textarea doesn't expose caret offsets to us
 * on every keystroke, we treat the trailing token of the whole value as
 * the active one. This matches user expectation: when the user types
 * `hello /re`, the menu pops up; if they then add a space, the menu closes.
 */
export function detectSlashCommand(value: string): SlashPickerMatch {
  const trimmed = value;
  const lastWhitespace = (() => {
    for (let i = trimmed.length - 1; i >= 0; i -= 1) {
      const ch = trimmed[i];
      if (ch === " " || ch === "\n" || ch === "\t") {
        return i;
      }
    }
    return -1;
  })();
  const trailing = trimmed.slice(lastWhitespace + 1);

  if (!trailing.startsWith("/")) {
    return { active: false, query: "", start: -1 };
  }

  return {
    active: true,
    query: trailing.slice(1),
    start: lastWhitespace + 1,
  };
}

/**
 * Compute the next highlighted index when the user presses ArrowUp /
 * ArrowDown. Wraps around at both ends.
 */
export function nextPickerIndex(
  current: number,
  total: number,
  direction: "up" | "down",
): number {
  if (total <= 0) {
    return -1;
  }
  if (current < 0) {
    return direction === "down" ? 0 : total - 1;
  }
  const next = current + (direction === "down" ? 1 : -1);
  if (next < 0) {
    return total - 1;
  }
  if (next >= total) {
    return 0;
  }
  return next;
}
