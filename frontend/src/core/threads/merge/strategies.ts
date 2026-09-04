import type { Message } from "@langchain/langgraph-sdk";

import { findLastMessageIndex } from "./identity";
import {
  isAlignmentNoiseMessage,
  isMessageInHistory,
  lastEquivalentIndex,
} from "./overlap";
import {
  messageIsAssistantSide,
  moveSingleTrailingHumanInputToFront,
  splitThreadForOptimisticHuman,
} from "./repair";

/**
 * When a historical thread is opened, run-event history is loaded
 * newest-run first. That suffix is not a prefix of checkpoint state, so
 * the streaming overlap finder fails and would otherwise prepend the
 * latest turn. Rebuild from the checkpoint prefix that precedes the
 * matched span, then the chronological history suffix.
 *
 * Returns `null` when the suffix-merge strategy cannot be applied (no
 * matching human message found in thread, or the first match is at
 * position 0 leaving no prefix to prepend).
 */
export function mergeHistoryAsThreadSuffix(
  historyMessages: Message[],
  threadMessages: Message[],
  optimisticMessages: Message[],
): Message[] | null {
  if (historyMessages.length === 0 || threadMessages.length === 0) {
    return null;
  }

  const lastHistoryHumanIndex = findLastMessageIndex(
    historyMessages,
    (message) =>
      message.type === "human" && !isAlignmentNoiseMessage(message),
  );
  if (lastHistoryHumanIndex === -1) {
    return null;
  }

  const lastHistoryHuman = historyMessages[lastHistoryHumanIndex]!;
  if (lastEquivalentIndex(threadMessages, lastHistoryHuman) < 0) {
    return null;
  }

  const matchIndexes = historyMessages
    .map((message) => lastEquivalentIndex(threadMessages, message))
    .filter((index) => index >= 0);
  if (matchIndexes.length === 0) {
    return null;
  }

  const firstMatch = Math.min(...matchIndexes);
  if (firstMatch <= 0) {
    return null;
  }

  const prefix = threadMessages.slice(0, firstMatch);
  // Filter the "after" segment (thread messages not in history) to exclude
  // hidden/noise messages such as DynamicContext reminders.  These reminders
  // are injected by middleware into checkpoint state but never appear in
  // run-event history, so they always land in `after`.  When they accumulate
  // after all of history, `repairDynamicContextUserMessageOrder` then moves
  // every `__user` copy to follow its reminder — pulling all user questions
  // to the bottom and stratifying the conversation into answers-first,
  // questions-last.  Since these messages are hidden from the UI anyway,
  // dropping them from `after` prevents the misordering.
  const after = threadMessages
    .slice(firstMatch)
    .filter(
      (message) =>
        !isMessageInHistory(message, historyMessages) &&
        !isAlignmentNoiseMessage(message),
    );

  return mergeThreadAndOptimisticMessages(
    prefix,
    [...historyMessages, ...after],
    optimisticMessages,
  );
}

/**
 * Merge the established thread prefix, the new thread segment, and
 * optimistic messages.
 *
 * When there are no human optimistic messages, the thread segment is
 * processed through `moveSingleTrailingHumanInputToFront` to handle
 * the streaming edge case where [AI..., human] is temporarily exposed.
 *
 * When there IS a human optimistic message, the thread segment is split
 * into established turns and a current tail.  The optimistic human is
 * inserted BEFORE the first streaming assistant message in the tail,
 * so it appears between the user's question and the AI's streaming
 * response.
 */
export function mergeThreadAndOptimisticMessages(
  establishedThreadPrefix: Message[],
  threadNewSegment: Message[],
  optimisticMessages: Message[],
): Message[] {
  const humanOptimistic = optimisticMessages.filter(
    (message) => message.type === "human",
  );
  const otherOptimistic = optimisticMessages.filter(
    (message) => message.type !== "human",
  );

  if (humanOptimistic.length === 0) {
    const currentTurnTail =
      moveSingleTrailingHumanInputToFront(threadNewSegment);
    return [...establishedThreadPrefix, ...currentTurnTail, ...otherOptimistic];
  }

  const { established: peeledEstablished, currentTail } =
    splitThreadForOptimisticHuman(threadNewSegment);
  const established = [...establishedThreadPrefix, ...peeledEstablished];
  const base = [...established, ...currentTail];

  const firstStreamingIndex = currentTail.findIndex(messageIsAssistantSide);
  if (firstStreamingIndex === -1) {
    return [...base, ...humanOptimistic, ...otherOptimistic];
  }

  const insertAt = established.length + firstStreamingIndex;
  return [
    ...base.slice(0, insertAt),
    ...humanOptimistic,
    ...otherOptimistic,
    ...base.slice(insertAt),
  ];
}
