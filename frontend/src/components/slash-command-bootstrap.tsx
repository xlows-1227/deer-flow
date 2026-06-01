"use client";

import { useEffect } from "react";

import { registerExampleCommands } from "@/core/slash-commands/examples";

/**
 * Client-only side effect: registers the bundled example slash commands
 * (`/demo-clear`, `/standup`, …) with the global registry once on mount.
 *
 * Render this exactly once, near the top of the React tree. It returns
 * `null`, so it has no visual effect.
 *
 * To add a new team-wide command:
 *   1. Drop a file in `src/core/slash-commands/examples/`.
 *   2. Export a `registerXxxCommand()` from it.
 *   3. Add a call to it inside `registerExampleCommands()` in
 *      `src/core/slash-commands/examples/index.ts`.
 *   4. (For app-local commands) call your `registerXxxCommand()` from
 *      this file or from a module that's imported at boot.
 */
export function SlashCommandBootstrap() {
  useEffect(() => {
    registerExampleCommands();
  }, []);
  return null;
}
