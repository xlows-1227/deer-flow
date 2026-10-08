import type { Message } from "@langchain/langgraph-sdk";

import {
  containsHumanSubsequence,
  findHistoryThreadOverlap,
  lastHistoryMatchThreadIndex,
  threadCoversHistory,
} from "./overlap";
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
  isLoading: boolean = true,
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

  if (threadOverlapLen === 0) {
    // When the thread (checkpoint state) still covers the run-event history,
    // the thread is the authoritative order. This must be tried BEFORE the
    // suffix-merge strategy: suffix-merge appends checkpoint-only messages
    // (clarification cards injected via Command(goto=END)) after the whole
    // history, displacing them to the very end of the conversation. Strict
    // positional overlap fails on such extras and on order divergence
    // (tool results journaled in completion order but stored in call
    // order), so threadCoversHistory falls back to human-anchored matching
    // with identity containment before declaring coverage.
    if (
      filteredHistory.length > 0 &&
      timestampedThreadMessages.length > 0 &&
      threadCoversHistory(timestampedThreadMessages, filteredHistory)
    ) {
      // Split at the deepest matched thread position so optimistic input is
      // inserted against the trailing (newer-than-history) segment, exactly
      // like the overlap path would.
      const boundary = lastHistoryMatchThreadIndex(
        timestampedThreadMessages,
        filteredHistory,
      );
      return finalizeMergedMessages(
        mergeThreadAndOptimisticMessages(
          timestampedThreadMessages.slice(0, boundary + 1),
          timestampedThreadMessages.slice(boundary + 1),
          optimisticMessages,
          isLoading,
        ),
        filteredHistory.length > 0,
      );
    }
    const suffixMerged = mergeHistoryAsThreadSuffix(
      filteredHistory,
      timestampedThreadMessages,
      optimisticMessages,
      isLoading,
    );
    if (suffixMerged) {
      return finalizeMergedMessages(
        suffixMerged,
        filteredHistory.length > 0,
      );
    }
    // Looser fallback: when the thread genuinely lacks some history
    // messages (removed ids, e.g. after summarization) but every history
    // question is still found in order inside the thread, use the thread
    // order directly so middleware-injected messages (clarification cards)
    // keep their correct position instead of being displaced to the end.
    if (
      filteredHistory.length > 0 &&
      timestampedThreadMessages.length > 0 &&
      containsHumanSubsequence(timestampedThreadMessages, filteredHistory)
    ) {
      const boundary = lastHistoryMatchThreadIndex(
        timestampedThreadMessages,
        filteredHistory,
      );
      return finalizeMergedMessages(
        mergeThreadAndOptimisticMessages(
          timestampedThreadMessages.slice(0, boundary + 1),
          timestampedThreadMessages.slice(boundary + 1),
          optimisticMessages,
          isLoading,
        ),
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
        isLoading,
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
  containsAsSubsequence,
  containsHumanSubsequence,
  isAlignmentNoiseMessage,
  findHistoryThreadOverlap,
  lastHistoryMatchThreadIndex,
  threadCoversHistory,
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
