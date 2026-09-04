import type { Message } from "@langchain/langgraph-sdk";

import {
  extractTextFromMessage,
  getMessageTimestamp,
} from "../../messages/utils";
import {
  normalizeHumanMessageText,
} from "./identity";
import { findHistoryThreadOverlap } from "./overlap";
import { finalizeMergedMessages } from "./repair";
import {
  mergeHistoryAsThreadSuffix,
  mergeThreadAndOptimisticMessages,
} from "./strategies";
import { mergeMissingTimestamps } from "./timestamps";

/**
 * Merge history messages, live thread messages, and optimistic messages
 * into a single chronological array.
 *
 * ## Algorithm
 *
 * 1. **Filter history**: Remove history human messages without a stable ID
 *    — they can't be deduplicated against thread copies (which have IDs
 *    from checkpoint), leading to duplicate Q messages with possibly stale
 *    content. The thread/checkpoint layer has the correct final human
 *    message content.
 *
 * 2. **Propagate timestamps**: Copy timestamps from history (which has
 *    `created_at` from run events) to thread messages that lack them.
 *
 * 3. **Find overlap**: Detect how much of the history suffix matches a
 *    prefix of the thread. Middleware-injected messages (summaries,
 *    reminders) are skipped during alignment.
 *
 * 4. **Merge**:
 *    - If overlap found: prepend non-overlapping history, then thread
 *      (overlap + new segment), then optimistic.
 *    - If no overlap: try suffix-merge strategy (find where history's last
 *      human matches in thread, split there). If that fails, fall back
 *      to filtering history against thread (only prepend genuinely absent
 *      messages).
 *
 * 5. **Finalize**: Deduplicate by identity, remove adjacent text-duplicate
 *    human messages, repair trailing turn order and dynamic-context
 *    user message order.
 */
export function mergeMessages(
  historyMessages: Message[],
  threadMessages: Message[],
  optimisticMessages: Message[],
): Message[] {
  // NOTE: We previously filtered out history human messages without a
  // stable ID to avoid duplicate Q messages.  But filtering causes those
  // Q to vanish from history — when overlap detection fails and we fall
  // back to suffix-merge, the thread's copy of that Q is treated as "not
  // in history" and lands at the END of `after`, producing the exact
  // "没时间的Q被落下来" misordering the user observed (Q without an ID
  // tend to also lack a timestamp).  Instead, keep all history messages
  // and rely on dedupeMessagesByIdentity + dedupeAdjacentHumanByText to
  // remove duplicates after merging.
  const filteredHistory = historyMessages.slice();

  // NOTE: We deliberately do NOT text-filter history human messages before
  // overlap detection.  The previous text-filter removed history human
  // messages whose text matched a thread human message, but this broke
  // the suffix-aligned overlap detection — without human messages in
  // history, the alignment couldn't find a match, causing ALL history
  // AI messages to be prepended before ALL thread messages (Q/A分层).
  //
  // Instead, findHistoryThreadOverlap uses messagesEquivalent() which
  // already does text-based matching for human messages (and text matching
  // for AI messages).  After merging, dedupeMessagesByText removes any
  // remaining text-duplicates that slip through when IDs differ (e.g.
  // DynamicContextMiddleware's msg-1 → msg-1__user rename).

  const timestampedThreadMessages = mergeMissingTimestamps(
    filteredHistory,
    threadMessages,
  );

  const { cutoff, threadOverlapLen } = findHistoryThreadOverlap(
    filteredHistory,
    timestampedThreadMessages,
  );

  // Only log when there's actual content to inspect — otherwise the initial
  // render (empty history & thread) spams the console and triggers the
  // Next.js dev error overlay.
  if (
    typeof window !== "undefined" &&
    (filteredHistory.length > 0 || timestampedThreadMessages.length > 0)
  ) {
    console.debug(
      "[mergeMessages] history vs thread overlap:",
      {
        historyLen: filteredHistory.length,
        threadLen: timestampedThreadMessages.length,
        cutoff,
        threadOverlapLen,
      },
      "history:",
      filteredHistory.map((m, i) => ({
        hIdx: i,
        type: m.type,
        id: m.id ?? "no-id",
        ts: getMessageTimestamp(m)?.slice(0, 19) ?? "null",
        text: m.type === "human"
          ? normalizeHumanMessageText(m)
          : extractTextFromMessage(m).slice(0, 40),
      })),
      "thread:",
      timestampedThreadMessages.map((m, i) => ({
        tIdx: i,
        type: m.type,
        id: m.id ?? "no-id",
        ts: getMessageTimestamp(m)?.slice(0, 19) ?? "null",
        text: m.type === "human"
          ? normalizeHumanMessageText(m)
          : extractTextFromMessage(m).slice(0, 40),
      })),
    );
  }

  if (threadOverlapLen === 0) {
    const suffixMerged = mergeHistoryAsThreadSuffix(
      filteredHistory,
      timestampedThreadMessages,
      optimisticMessages,
    );
    if (typeof window !== "undefined") {
      console.debug(
        "[mergeMessages] mergeHistoryAsThreadSuffix result:",
        suffixMerged ? "SUCCESS" : "null (fallback to filter)",
      );
    }
    if (suffixMerged) {
      return finalizeMergedMessages(
        suffixMerged,
        filteredHistory.length > 0,
      );
    }
  }

  const establishedThreadPrefix = timestampedThreadMessages.slice(
    0,
    threadOverlapLen,
  );
  const threadNewSegment = timestampedThreadMessages.slice(threadOverlapLen);

  return finalizeMergedMessages(
    [
      ...filteredHistory.slice(0, cutoff),
      ...mergeThreadAndOptimisticMessages(
        establishedThreadPrefix,
        threadNewSegment,
        optimisticMessages,
      ),
    ],
    filteredHistory.length > 0,
  );
}

// Re-export public API
export { messageIdentity, messagesEquivalent, normalizeHumanMessageText } from "./identity";
export { dedupeMessagesByIdentity, dedupeAdjacentHumanByText } from "./identity";
export {
  humanMessageFilesSignature,
  humanMessageVisibilityKey,
  getHumanMessageVisibilityKeys,
} from "./identity";
export {
  isAlignmentNoiseMessage,
  findHistoryThreadOverlap,
} from "./overlap";
export {
  mergeHistoryAsThreadSuffix,
  mergeThreadAndOptimisticMessages,
} from "./strategies";
export {
  finalizeMergedMessages,
  repairTrailingTurnOrder,
  getMessageSeq,
  sortMessagesByTime,
} from "./repair";
export { withMessageTimestamp, mergeMissingTimestamps } from "./timestamps";
export {
  getVisibleOptimisticMessagesForServerMessages,
  getVisibleOptimisticMessages,
  getMessagesAfterBaseline,
  hasServerReplacementForOptimisticHuman,
} from "./optimistic";
