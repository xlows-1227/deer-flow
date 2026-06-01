"use client";

// ----------------------------------------------------------------------------
// Slash-command registry
// ----------------------------------------------------------------------------
//
// A tiny module-scoped registry. Built-in commands register themselves at
// import time (see `./builtin`); third-party / team plugins can call
// `registerSlashCommand` at module load to add their own.
//
// The registry is intentionally global (not React context) so that custom
// commands can be registered at app boot from anywhere, and so the picker
// can read the current list synchronously. We keep it that way deliberately
// to make the extension API trivial — the cost is that hot-reload may
// accumulate duplicates; the registry deduplicates by `id`.

import type { SlashCommand } from "./types";

/**
 * Internal store. Exported for tests; production code should use the
 * `registerSlashCommand` / `getSlashCommands` / `filterSlashCommands` API.
 */
const store: SlashCommand[] = [];

/** Tracks ids that have been registered in this process lifetime. */
const knownIds = new Set<string>();

/**
 * Register a slash command. Idempotent on `id`: re-registering the same
 * id updates the existing entry instead of appending a duplicate.
 */
export function registerSlashCommand(command: SlashCommand): void {
  const existing = store.findIndex((c) => c.id === command.id);
  if (existing >= 0) {
    store[existing] = command;
  } else {
    store.push(command);
  }
  knownIds.add(command.id);
}

/** Remove a single command (mostly useful for tests / hot-reload). */
export function unregisterSlashCommand(id: string): void {
  const idx = store.findIndex((c) => c.id === id);
  if (idx >= 0) {
    store.splice(idx, 1);
  }
  knownIds.delete(id);
}

/** Snapshot of all registered commands. */
export function getSlashCommands(): readonly SlashCommand[] {
  // Defensive copy so callers can't mutate the registry.
  return store.slice();
}

/** Test helper. Wipe the registry between test cases. */
export function __resetSlashCommandRegistry(): void {
  store.length = 0;
  knownIds.clear();
}

/**
 * Filter registered commands by a slash query. Case-insensitive substring
 * match against `name`, `aliases`, and `keywords`. Empty query returns
 * everything.
 */
export function filterSlashCommands(
  query: string,
  commands: readonly SlashCommand[] = store,
): SlashCommand[] {
  const trimmed = query.trim();
  if (trimmed.length === 0) {
    return commands.slice();
  }
  const needle = trimmed.toLowerCase();
  return commands.filter((c) => {
    if (c.name.toLowerCase().includes(needle)) {
      return true;
    }
    if (c.aliases) {
      for (const a of c.aliases) {
        if (a.toLowerCase().includes(needle)) {
          return true;
        }
      }
    }
    if (c.keywords) {
      for (const k of c.keywords) {
        if (k.toLowerCase().includes(needle)) {
          return true;
        }
      }
    }
    return false;
  });
}
