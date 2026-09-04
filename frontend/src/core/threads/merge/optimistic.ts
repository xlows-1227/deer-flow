import type { Message } from "@langchain/langgraph-sdk";

import {
  humanMessageFilesSignature,
  humanMessageVisibilityKey,
  messageIdentity,
  normalizeHumanMessageText,
} from "./identity";

/**
 * Check if any server message replaces an optimistic human message.
 *
 * Matches by text content or file signature, excluding messages that are
 * in the baseline (already existed before the optimistic message was created).
 */
export function hasServerReplacementForOptimisticHuman(
  optimisticMessages: Message[],
  baselineHumanMessageKeys: ReadonlySet<string>,
  serverMessages: Message[],
): boolean {
  const optimisticHumanMessages = optimisticMessages.filter(
    (message) => message.type === "human",
  );
  if (optimisticHumanMessages.length === 0) return false;

  const optimisticHumanTexts = new Set(
    optimisticHumanMessages
      .map(normalizeHumanMessageText)
      .filter((text) => text.length > 0),
  );
  const optimisticFileSigs = new Set(
    optimisticHumanMessages
      .map(humanMessageFilesSignature)
      .filter((sig): sig is string => Boolean(sig)),
  );

  if (optimisticHumanTexts.size === 0 && optimisticFileSigs.size === 0) {
    return false;
  }

  return serverMessages.some((message) => {
    if (message.type !== "human") {
      return false;
    }
    const key = humanMessageVisibilityKey(message);
    if (key && baselineHumanMessageKeys.has(key)) {
      return false;
    }
    const text = normalizeHumanMessageText(message);
    if (text && optimisticHumanTexts.has(text)) return true;
    const fileSig = humanMessageFilesSignature(message);
    if (fileSig && optimisticFileSigs.has(fileSig)) return true;
    return false;
  });
}

/**
 * Filter optimistic messages to only show those that haven't been replaced
 * by server messages.
 */
export function getVisibleOptimisticMessagesForServerMessages(
  optimisticMessages: Message[],
  baselineHumanMessageKeys: ReadonlySet<string>,
  serverMessages: Message[],
): Message[] {
  const hasHumanOptimistic = optimisticMessages.some(
    (message) => message.type === "human",
  );

  if (hasHumanOptimistic) {
    const hasReplacement = hasServerReplacementForOptimisticHuman(
      optimisticMessages,
      baselineHumanMessageKeys,
      serverMessages,
    );
    if (hasReplacement) {
      return [];
    }
  }
  return optimisticMessages;
}

/**
 * Get visible optimistic messages based on human message count change.
 *
 * If there's a human optimistic message and the server's human count has
 * increased, hide the optimistic message (server has echoed it).
 */
export function getVisibleOptimisticMessages(
  optimisticMessages: Message[],
  previousHumanMessageCount: number,
  currentHumanMessageCount: number,
): Message[] {
  if (
    optimisticMessages.some((message) => message.type === "human") &&
    currentHumanMessageCount > previousHumanMessageCount
  ) {
    return [];
  }
  return optimisticMessages;
}

/**
 * Get messages that arrived after the baseline (used for token usage
 * accounting during streaming).
 */
export function getMessagesAfterBaseline(
  messages: Message[],
  baselineMessageIds: ReadonlySet<string>,
): Message[] {
  return messages.filter((message) => {
    const id = messageIdentity(message);
    return !id || !baselineMessageIds.has(id);
  });
}

// Re-export identity helpers needed by external consumers.
export { messageIdentity } from "./identity";
