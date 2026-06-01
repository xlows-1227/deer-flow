"use client";

// ----------------------------------------------------------------------------
// `standup-demo` — a realistic custom command
// ----------------------------------------------------------------------------
//
// This shows what a "real" custom command looks like in production: it
// takes a query argument (the teammate's name, or a date), templates it
// into a standup prompt, and *replaces* the user's input with the
// expanded template rather than clearing it.
//
// The handler is a stub — it doesn't talk to any backend yet. In a real
// deployment you'd swap the `template` for a network call, a workflow
// invocation, or whatever your platform exposes. The shape stays the
// same.

import { registerSlashCommand, type SlashCommand } from "../index";

/**
 * Build the standup demo command without registering it. Exported for
 * tests.
 */
export function buildStandupDemoCommand(): SlashCommand {
  return {
    id: "demo:standup",
    name: "standup",
    aliases: ["daily", "scrum"],
    kind: "custom",
    value: null,
    label: "Daily standup",
    description: "Insert a standup template, optionally for a named teammate",
    keywords: ["meeting", "team", "demo"],

    run(query) {
      // 1. Parse the query. The host hands us whatever the user typed
      //    after `/standup` (whitespace-trimmed by `filterSlashCommands`,
      //    but not otherwise processed). We split on whitespace and
      //    interpret the first token as the teammate name; anything
      //    else we treat as extra context.
      const trimmed = query.trim();
      const [name, ...rest] = trimmed.length > 0 ? trimmed.split(/\s+/) : [];
      const context = rest.join(" ").trim();

      // 2. Build the template. In production this is where you'd call
      //    a backend, a workflow engine, or a LLM to *generate* the
      //    standup — for the demo we just stamp a skeleton.
      const today = new Date().toISOString().slice(0, 10);
      const who = name && name.length > 0 ? name : "the team";
      const lines = [
        `# Standup — ${today}`,
        ``,
        `**For:** ${who}${context ? ` (${context})` : ""}`,
        ``,
        `## Yesterday`,
        `- `,
        ``,
        `## Today`,
        `- `,
        ``,
        `## Blockers`,
        `- `,
      ];
      const template = lines.join("\n");

      // 3. Return the template. The host will put it in the input box
      //    *in place of* the slash token, so the user sees a fully
      //    formed message ready to edit and send. Returning a string
      //    is the "I want to pre-fill the input" code path.
      return template;
    },
  };
}

/** Register the standup demo command with the global registry. */
export function registerStandupDemoCommand(): void {
  registerSlashCommand(buildStandupDemoCommand());
}
