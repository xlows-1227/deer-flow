import type { Message, Run } from "@langchain/langgraph-sdk";

import { extractTextFromMessage } from "../../messages/utils";
import { dedupeAdjacentHumanByText, dedupeMessagesByIdentity, normalizeHumanMessageText } from "../merge/identity";
import { withMessageTimestamp } from "../merge/timestamps";

export type LoadedRunMessage = {
  seq: number;
  message: Message;
};

/**
 * Parse the `created_at` timestamp from a run into milliseconds.
 *
 * Returns 0 when the timestamp is missing or unparseable, which sorts
 * before any valid timestamp.
 */
export function getRunCreatedAtMs(run: Run): number {
  const createdAt = (run as { created_at?: string | null }).created_at;
  if (!createdAt) {
    return 0;
  }
  const timestamp = Date.parse(createdAt);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

/**
 * Sort runs chronologically (oldest first) by `created_at`.
 *
 * The backend returns runs newest-first (created_at DESC), but we need
 * oldest-first to compose messages in chronological order.
 */
export function sortRunsChronologically(runs: Run[]): Run[] {
  return [...runs].sort((a, b) => getRunCreatedAtMs(a) - getRunCreatedAtMs(b));
}

/**
 * Find the index of the latest (newest) run that hasn't been loaded yet.
 *
 * Runs are sorted oldest-first after `sortRunsChronologically`, so we
 * search from the end backwards. Returns -1 when all runs are loaded.
 */
export function findLatestUnloadedRunIndex(
  runs: Run[],
  loadedRunIds: ReadonlySet<string>,
): number {
  for (let i = runs.length - 1; i >= 0; i--) {
    const run = runs[i];
    if (run && !loadedRunIds.has(run.run_id)) {
      return i;
    }
  }
  return -1;
}

/**
 * Compose a single message array from all loaded run messages.
 *
 * ALWAYS sorts by run creation time first, then by run-internal order.
 * Each run's `seq` counter starts from 0 independently, so cross-run
 * seq values are NOT globally comparable — sorting all messages by
 * bare seq collapses seq=0 of every run together and produces chaos.
 *
 * The chronological run path below already preserves backend-returned
 * seq ordering within each run (stable sort of flatMap), which is
 * exactly what we want.
 */
export function mergeLoadedRunMessages(
  runs: Run[],
  messagesByRunId: ReadonlyMap<string, LoadedRunMessage[]>,
  appendedMessages: Message[] = [],
): Message[] {
  const sortedRuns = sortRunsChronologically(runs);
  if (typeof window !== "undefined") {
    console.debug(
      "[mergeLoadedRunMessages] runs (after sort):",
      sortedRuns.map((r, i) => ({
        sortIdx: i,
        run_id: r.run_id?.slice(0, 8),
        created_at: (r as { created_at?: string }).created_at,
        parsedMs: getRunCreatedAtMs(r),
        msgCount: messagesByRunId.get(r.run_id)?.length ?? 0,
      })),
    );
  }
  const orderedMessages = sortedRuns.flatMap((run) =>
    (messagesByRunId.get(run.run_id) ?? []).map((entry) => entry.message),
  );

  const deduped = dedupeAdjacentHumanByText(
    dedupeMessagesByIdentity([
      ...orderedMessages,
      ...appendedMessages,
    ]),
  );
  if (typeof window !== "undefined") {
    console.debug(
      "[mergeLoadedRunMessages] orderedMessages (pre-dedup):",
      orderedMessages.map((m, i) => ({
        idx: i,
        type: m.type,
        id: m.id ?? "no-id",
        text: m.type === "human"
          ? normalizeHumanMessageText(m)
          : extractTextFromMessage(m).slice(0, 40),
      })),
    );
    console.debug(
      "[mergeLoadedRunMessages] deduped result:",
      deduped.map((m, i) => ({
        idx: i,
        type: m.type,
        id: m.id ?? "no-id",
        text: m.type === "human"
          ? normalizeHumanMessageText(m)
          : extractTextFromMessage(m).slice(0, 40),
      })),
    );
  }
  return deduped;
}

// Re-export timestamp helper used by useThreadHistory
export { withMessageTimestamp };
