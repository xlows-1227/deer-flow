import type { Message } from "@langchain/langgraph-sdk";

import { getMessageTimestamp } from "../../messages/utils";
import { isHiddenFromUIMessage } from "../../messages/utils";
import {
  messageIdentity,
  messagesEquivalent,
  normalizeHumanMessageText,
  timestampsAreClose,
} from "./identity";

/**
 * Messages that middlewares inject into checkpoint state (summarization
 * summaries, loop warnings, todo reminders, dynamic-context placeholders)
 * never appear in run-event history, so they must not participate in
 * history/thread overlap alignment — otherwise a single summary message at
 * the head of the live state breaks the strict positional match and the
 * whole thread gets re-appended after history.
 */
export function isAlignmentNoiseMessage(message: Message): boolean {
  return isHiddenFromUIMessage(message);
}

/**
 * Find the first index of a message equivalent to `target`.
 */
export function firstEquivalentIndex(
  messages: Message[],
  target: Message,
): number {
  for (let index = 0; index < messages.length; index += 1) {
    if (messagesEquivalent(messages[index]!, target)) {
      return index;
    }
  }
  return -1;
}

/**
 * Find the last index of a message equivalent to `target`.
 */
export function lastEquivalentIndex(
  messages: Message[],
  target: Message,
): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messagesEquivalent(messages[index]!, target)) {
      return index;
    }
  }
  return -1;
}

/**
 * Check if a message already exists in history.
 *
 * When the message has a stable identity (id/tool_call_id), match by identity
 * first.  If that fails, fall back to text + timestamp matching against
 * history messages that ALSO lack a stable ID (run-event copies with null
 * id).  The timestamp check (using !== so null≠"05:51" = different)
 * prevents Q3 from matching Q4 when the user asks the same question twice.
 */
export function isMessageInHistory(
  message: Message,
  historyMessages: Message[],
): boolean {
  const messageId = messageIdentity(message);
  if (messageId) {
    // 1. Identity-only match (fast path)
    const identityMatch = historyMessages.some(
      (historyMessage) => messageIdentity(historyMessage) === messageId,
    );
    if (identityMatch) return true;

    // 2. Text + timestamp fallback: match against history messages
    // that lack a stable ID (run-event copies whose id is null).
    // Use a tolerance window because history (run-event) and thread
    // (checkpoint) copies of the same message carry different timestamps.
    const messageTs = getMessageTimestamp(message) ?? null;
    return historyMessages.some((historyMessage) => {
      if (messageIdentity(historyMessage)) return false; // skip history msgs with IDs
      if (message.type !== historyMessage.type) return false;
      if (message.type === "human") {
        const messageText = normalizeHumanMessageText(message);
        const historyText = normalizeHumanMessageText(historyMessage);
        if (messageText.length === 0 || messageText !== historyText) {
          return false;
        }
        const historyTs = getMessageTimestamp(historyMessage) ?? null;
        return timestampsAreClose(historyTs, messageTs);
      }
      return false;
    });
  }
  // No stable ID: fall back to messagesEquivalent (text + timestamp)
  return historyMessages.some((historyMessage) =>
    messagesEquivalent(historyMessage, message),
  );
}

/**
 * Check whether every history message appears, in order, within the thread.
 *
 * Greedy subsequence test used by `mergeMessages` as a last-resort guard:
 * when strict overlap fails but the thread (checkpoint state) still contains
 * the whole run-event history as an ordered subsequence, the thread is the
 * authoritative order and can be used directly.
 */
export function containsAsSubsequence(
  threadMessages: Message[],
  historyMessages: Message[],
): boolean {
  if (historyMessages.length === 0) {
    return true;
  }
  let historyIndex = 0;
  for (const threadMessage of threadMessages) {
    if (historyIndex >= historyMessages.length) {
      break;
    }
    if (messagesEquivalent(historyMessages[historyIndex]!, threadMessage)) {
      historyIndex += 1;
    }
  }
  return historyIndex >= historyMessages.length;
}

/**
 * Check whether every history HUMAN message appears, in order, within the
 * thread — matched only by normalized text + timestamp tolerance.
 *
 * Used by `mergeMessages` as the final fallback when the strict
 * ``containsAsSubsequence`` (id + text) fails.  The run-event history
 * serializes AI messages with a trailing ``[工具调用: ...]`` marker and
 * may also assign them a different id than the checkpoint copy, so
 * ``messagesEquivalent`` cannot reliably align AI messages across the two
 * sources.  Human messages, however, are echoed back verbatim (modulo the
 * ``__user`` rename handled by ``normalizeHumanMessageText``), so matching
 * only on human text is stable.  When every history question can be found
 * in order inside the thread, the thread (checkpoint) is the authoritative
 * order and should be used directly so middleware-injected messages (the
 * clarification card) keep their correct position instead of being
 * displaced to the end by the ``[...history, ...thread]`` dedupe pass.
 *
 * Returns ``false`` when ``historyMessages`` has no human message — without
 * a human anchor this check would trivially pass for empty histories and
 * mask real misalignment, so callers should rely on
 * ``containsAsSubsequence`` for the empty/all-AI case instead.
 */
export function containsHumanSubsequence(
  threadMessages: Message[],
  historyMessages: Message[],
): boolean {
  const historyHumanMessages = historyMessages.filter(
    (message) => message.type === "human",
  );
  if (historyHumanMessages.length === 0) {
    return false;
  }
  let historyIndex = 0;
  for (const threadMessage of threadMessages) {
    if (historyIndex >= historyHumanMessages.length) {
      break;
    }
    const historyMessage = historyHumanMessages[historyIndex]!;
    if (threadMessage.type !== "human") {
      continue;
    }
    const historyText = normalizeHumanMessageText(historyMessage);
    const threadText = normalizeHumanMessageText(threadMessage);
    if (
      historyText.length === 0 ||
      threadText.length === 0 ||
      historyText !== threadText
    ) {
      continue;
    }
    const historyTs = getMessageTimestamp(historyMessage) ?? null;
    const threadTs = getMessageTimestamp(threadMessage) ?? null;
    if (timestampsAreClose(historyTs, threadTs)) {
      historyIndex += 1;
    }
  }
  return historyIndex >= historyHumanMessages.length;
}

/**
 * Find the deepest thread index any history message matches.
 *
 * Used by `mergeMessages` to split the thread into an "established" prefix
 * (already covered by history) and a trailing segment (messages newer than
 * anything in history, e.g. in-flight streaming output) so optimistic
 * messages are inserted the same way the overlap path would insert them.
 */
export function lastHistoryMatchThreadIndex(
  threadMessages: Message[],
  historyMessages: Message[],
): number {
  let boundary = -1;
  for (const historyMessage of historyMessages) {
    const index = lastEquivalentIndex(threadMessages, historyMessage);
    if (index > boundary) {
      boundary = index;
    }
  }
  return boundary;
}

/**
 * Decide whether the thread (checkpoint state) covers the whole run-event
 * history, making the thread the authoritative order.
 *
 * True when every history HUMAN message appears in order in the thread AND
 * every identity-bearing history message (AI id / tool_call_id) exists
 * somewhere in the thread.
 *
 * Run-event and checkpoint copies of the same AI message can serialize
 * differently (e.g. a ``[工具调用: ...]`` marker moved from tail to head)
 * and tool results are journaled in completion order but stored in call
 * order, so strict equivalence fails even though the thread holds every
 * message. Human messages are echoed back verbatim (modulo the ``__user``
 * rename), so they anchor the order check; identity containment guards
 * against the thread actually lacking messages (e.g. summarization removed
 * old turns, or checkpoint ids diverge from run-event ids) — in those cases
 * history still contributes content and the suffix-merge strategy should
 * run instead.
 */
export function threadCoversHistory(
  threadMessages: Message[],
  historyMessages: Message[],
): boolean {
  if (historyMessages.length === 0 || threadMessages.length === 0) {
    return false;
  }
  if (!containsHumanSubsequence(threadMessages, historyMessages)) {
    return false;
  }
  const threadIdentities = new Set<string>();
  for (const threadMessage of threadMessages) {
    const identity = messageIdentity(threadMessage);
    if (identity) {
      threadIdentities.add(identity);
    }
  }
  for (const historyMessage of historyMessages) {
    const identity = messageIdentity(historyMessage);
    if (identity && !threadIdentities.has(identity)) {
      return false;
    }
  }
  return true;
}

/**
 * Find the overlap between history messages and thread messages.
 *
 * History is a suffix-aligned prefix of thread. Match by id when available
 * and by human text when run-event ids differ from live thread state.
 * Middleware-injected messages (summaries, reminders) are skipped during
 * alignment.
 *
 * The history suffix must anchor at the thread's FIRST alignable message,
 * but the thread may interleave extra messages the history lacks — e.g. the
 * ask_clarification ToolMessage injected by ClarificationMiddleware via
 * Command(goto=END) never produces a run event, so the checkpoint has a
 * copy the run-event history does not. Strict positional equality fails on
 * any such extra message, the overlap collapses to zero, and the fallback
 * merge appends checkpoint-only messages (the clarification card, duplicate
 * question copies) to the very end of the conversation. Requiring only
 * in-order coverage (skipping unmatched thread messages) keeps the overlap
 * intact while the anchor-at-start rule still prevents a short suffix from
 * spuriously matching a mid-conversation message.
 *
 * Returns:
 *   - `cutoff`: index in historyMessages where the non-overlapping prefix ends
 *   - `threadOverlapLen`: number of thread messages (including noise) that overlap
 */
export function findHistoryThreadOverlap(
  historyMessages: Message[],
  threadMessages: Message[],
): { cutoff: number; threadOverlapLen: number } {
  const alignableHistoryIndexes: number[] = [];
  historyMessages.forEach((message, index) => {
    if (!isAlignmentNoiseMessage(message)) {
      alignableHistoryIndexes.push(index);
    }
  });
  const alignableThreadIndexes: number[] = [];
  threadMessages.forEach((message, index) => {
    if (!isAlignmentNoiseMessage(message)) {
      alignableThreadIndexes.push(index);
    }
  });

  const maxOverlap = Math.min(
    alignableHistoryIndexes.length,
    alignableThreadIndexes.length,
  );
  for (let overlapLen = maxOverlap; overlapLen >= 1; overlapLen -= 1) {
    const historyStart = alignableHistoryIndexes.length - overlapLen;
    // Greedy in-order match over the thread prefix. Unmatched thread
    // messages are skipped (checkpoint-only extras such as clarification
    // cards); the first match must anchor at thread position 0.
    let matched = 0;
    let lastMatchThreadIndex = -1;
    for (
      let threadPos = 0;
      threadPos < alignableThreadIndexes.length && matched < overlapLen;
      threadPos += 1
    ) {
      const historyMessage =
        historyMessages[alignableHistoryIndexes[historyStart + matched]!]!;
      const threadMessage = threadMessages[alignableThreadIndexes[threadPos]!]!;
      if (messagesEquivalent(historyMessage, threadMessage)) {
        if (matched === 0 && threadPos !== 0) {
          break;
        }
        matched += 1;
        lastMatchThreadIndex = alignableThreadIndexes[threadPos]!;
      }
    }
    if (matched === overlapLen && lastMatchThreadIndex >= 0) {
      return {
        cutoff: alignableHistoryIndexes[historyStart]!,
        threadOverlapLen: lastMatchThreadIndex + 1,
      };
    }
  }
  return { cutoff: historyMessages.length, threadOverlapLen: 0 };
}
