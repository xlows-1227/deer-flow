// ----------------------------------------------------------------------------
// @-mention picker parsing helpers
// ----------------------------------------------------------------------------
//
// Pure functions used by the chat input's `@` file picker. The picker itself
// is candidate-agnostic — it shows a list of file suggestions and lets the
// user pick one. The detection / wrapping helpers below live here because
// they're tied to *typing* an `@` token, not to any specific file.
//
// The implementation mirrors `slash-picker.ts` so that swapping between
// trigger characters is a one-line difference for the host component.

export interface MentionPickerMatch {
  /** Whether the input currently has an active `@` mention. */
  active: boolean;
  /**
   * The query after the leading `@`. The host filters the candidate list
   * with this string (case-insensitive substring against the file name).
   */
  query: string;
  /**
   * 0-based start offset of the `@` token in the input. Useful for
   * removing the typed token once the user picks a file.
   */
  start: number;
}

/**
 * Detect whether `value` ends with an `@` mention token. A mention is a
 * token that:
 *   - starts with `@`,
 *   - contains no whitespace,
 *   - is at the end of the input (i.e. the user is still typing it).
 *
 * The token is allowed to be just `@` (empty query), and we don't trigger
 * on `@` that appears in the middle of other text — only at the active
 * caret position. Because the textarea doesn't expose caret offsets to us
 * on every keystroke, we treat the trailing token of the whole value as
 * the active one, matching the slash-picker convention.
 *
 * Unlike the slash picker, an `@` may legitimately appear inside an email
 * address (e.g. `user@example`). We treat that as an in-progress mention
 * — typing a space after `user@example` closes the picker, just like the
 * slash picker closes on whitespace.
 */
export function detectMention(value: string): MentionPickerMatch {
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

  if (!trailing.startsWith("@")) {
    return { active: false, query: "", start: -1 };
  }

  return {
    active: true,
    query: trailing.slice(1),
    start: lastWhitespace + 1,
  };
}
