// ----------------------------------------------------------------------------
// Slash-command types
// ----------------------------------------------------------------------------
//
// A "slash command" is anything the user can summon in the chat input by
// typing `/<name>`. The picker is generic; each command declares its
// `kind` and any extra metadata so the host component (the chat input)
// can route the selected command to the right handler.

import type { ReactNode } from "react";

/**
 * Discrete categories. The host uses `kind` to decide which action to
 * take when the user selects a row. Keep this list small — a new kind
 * means adding a new branch in the host.
 */
export type SlashCommandKind =
  /** Switch the active skill. `value` is the skill name (or `null` for Auto). */
  | "skill"
  /** Switch the agent mode (flash / thinking / pro / ultra). `value` is the mode id. */
  | "mode"
  /** Open the model picker dialog. `value` is the model name. */
  | "model"
  /** Clear the current input. `value` is unused. */
  | "clear"
  /** Show in-picker help / list all commands. `value` is unused. */
  | "help"
  /**
   * Free-form custom command. The host invokes `run` with the trailing
   * query string (e.g. for `/standup 2024-10` -> `run("2024-10")`).
   * Custom commands are the extension point for team plugins.
   */
  | "custom";

export interface SlashCommand {
  /** Stable id, used as React key and in telemetry. */
  id: string;
  /**
   * What the user types after the leading `/`. Case-insensitive match.
   * `aliases` are alternate spellings (e.g. `["m", "model"]`).
   */
  name: string;
  aliases?: string[];
  kind: SlashCommandKind;
  /**
   * For `kind: "skill" | "mode" | "model"`: the target identifier to apply.
   * For `kind: "clear" | "help"`: unused, set to `null`.
   * For `kind: "custom"`: arbitrary payload handed to `run`.
   */
  value: string | null;
  /** Localized label shown in the picker row. */
  label: string;
  /** Localized description shown under the label. */
  description?: string;
  /** Optional icon shown in the picker row. */
  icon?: ReactNode;
  /**
   * Optional handler for `kind: "custom"`. The host invokes this when the
   * user selects the row. Returns the (possibly new) query to leave in
   * the input — typically `""` so the input is cleared.
   */
  run?: (query: string) => void | string | Promise<void | string>;
  /**
   * Extra search keywords — useful for custom commands that should match
   * words other than their `name` (e.g. `/standup` matching "daily",
   * "meeting", "scrum").
   */
  keywords?: string[];
}
