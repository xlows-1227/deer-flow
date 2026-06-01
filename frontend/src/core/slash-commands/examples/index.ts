"use client";

// ----------------------------------------------------------------------------
// Slash-command examples — entry point
// ----------------------------------------------------------------------------
//
// All demo commands live in this folder. `registerExampleCommands()`
// registers them with the global registry; call it once at app boot.
//
// The two demos we ship:
//
//   1. `/demo-clear` (aliases: dclear, wipe-demo)
//      Teaches the API: every field, the run() contract, the
//      return-value semantics, etc. Simplest possible custom command.
//
//   2. `/standup` (aliases: daily, scrum)
//      Realistic command: takes a query, returns a template. Drop-in
//      shape for team commands that pre-fill the input box.

import { registerClearDemoCommand } from "./clear-demo";
import { registerStandupDemoCommand } from "./standup-demo";

/** Register all built-in example commands. Safe to call multiple times. */
export function registerExampleCommands(): void {
  registerClearDemoCommand();
  registerStandupDemoCommand();
}

// Re-export builders + register helpers so tests / docs can poke at
// the shapes and individual demos can be registered standalone.
export { buildClearDemoCommand, registerClearDemoCommand } from "./clear-demo";
export {
  buildStandupDemoCommand,
  registerStandupDemoCommand,
} from "./standup-demo";
