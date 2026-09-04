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
 * Find the overlap between history messages and thread messages.
 *
 * History is a suffix-aligned prefix of thread. Match by id when available
 * and by human text when run-event ids differ from live thread state.
 * Middleware-injected messages (summaries, reminders) are skipped during
 * alignment.
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
    const matches = alignableThreadIndexes
      .slice(0, overlapLen)
      .every((threadIndex, offset) =>
        messagesEquivalent(
          historyMessages[alignableHistoryIndexes[historyStart + offset]!]!,
          threadMessages[threadIndex]!,
        ),
      );
    if (matches) {
      return {
        cutoff: alignableHistoryIndexes[historyStart]!,
        threadOverlapLen: alignableThreadIndexes[overlapLen - 1]! + 1,
      };
    }
  }
  return { cutoff: historyMessages.length, threadOverlapLen: 0 };
}
