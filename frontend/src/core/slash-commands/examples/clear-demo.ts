"use client";

// ----------------------------------------------------------------------------
// `clear-demo` — a tutorial slash command
// ----------------------------------------------------------------------------
//
// This is the simplest possible custom command: it clears the chat input,
// but it does so via `kind: "custom"` + a `run` callback rather than going
// through the built-in `meta:clear` path. The point is to *show* what each
// field of `SlashCommand` does and how a custom `run` interacts with the
// host (the chat input).
//
// We deliberately pick a different `name` ("demo-clear") so we don't
// collide with the built-in `meta:clear`. If you really want to override
// a built-in, just use a different `id` and the same `name` — the host
// will show both rows in the picker and the user can pick which to run.

import { registerSlashCommand, type SlashCommand } from "../index";

/**
 * Build the demo command object without registering it. Exported so tests
 * can inspect the shape (e.g. to assert the right `id` / `name` are set).
 */
export function buildClearDemoCommand(): SlashCommand {
  return {
    // Stable id. Two commands can share a `name` (the picker groups them
    // by name for filtering) but never an `id` — `registerSlashCommand`
    // is idempotent on `id` and will replace any existing entry.
    id: "demo:clear",

    // What the user types. Lowercase is convention but the filter is
    // case-insensitive, so "Demo-Clear" still matches `/demo-clear`.
    name: "demo-clear",

    // Alternate spellings the user can type. Each one is matched as if
    // the user had typed `name`.
    aliases: ["dclear", "wipe-demo"],

    // `kind` tells the host what to do when the user selects this row.
    // `"custom"` is the extension point: the host calls `run(query)`
    // and uses whatever the callback returns (or doesn't return) to
    // decide what ends up in the textarea afterwards.
    kind: "custom",

    // Unused for `"custom"` — set to `null` for clarity. The host
    // dispatches by `kind`, not by `value`, so leaving it `null` is fine.
    value: null,

    // Shown in the picker. Keep it short — the row only has ~28 chars of
    // horizontal room before the description wraps.
    label: "Clear input (demo)",

    // Subtitle / tooltip. Markdown is NOT supported; this is plain text.
    description:
      "Tutorial: a custom /clear that shows how the run() callback works",

    // Extra search keywords beyond `name` + `aliases`. Useful when the
    // command should be discoverable by synonyms — e.g. a `/standup`
    // command could add ["scrum", "meeting", "daily"] here.
    keywords: ["tutorial", "example", "demo"],

    // The handler. Receives the query text after the command name, e.g.
    //   `/demo-clear foo bar` -> `run("foo bar")`
    //   `/demo-clear`         -> `run("")`
    //
    // Return value contract:
    //   - `undefined` -> the host clears the input (default behavior).
    //   - a string    -> the host puts that string in the input box
    //                   (use this when your command inserts a template).
    //   - a Promise   -> the host awaits; the resolved value follows the
    //                   same rules as above.
    //
    // `run` is the *only* hook a custom command gets. There is no
    // "beforeSelect" / "afterSelect" / etc. — keep your command focused
    // on one user-visible action.
    run(query) {
      // We don't need the query here, but we log it so the demo is
      // discoverable in dev tools. In production you might forward the
      // query to a backend, use it as a parameter, etc.
      if (query.length > 0) {
        console.info(
          "[demo:clear] run() was called with query:",
          JSON.stringify(query),
        );
      }
      // Returning `undefined` tells the host: "I did my thing, now do
      // the default post-action (clear the input box)". This is the
      // common case for action-style commands.
      return undefined;
    },
  };
}

/** Register the demo command with the global registry. */
export function registerClearDemoCommand(): void {
  registerSlashCommand(buildClearDemoCommand());
}
