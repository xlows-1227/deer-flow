import type { Message } from "@langchain/langgraph-sdk";

import { getMessageTimestamp } from "../../messages/utils";
import { messageIdentity, normalizeHumanMessageText } from "./identity";

/**
 * Attach a timestamp to a message's `additional_kwargs` if it doesn't
 * already have one.
 */
export function withMessageTimestamp(
  message: Message,
  timestamp?: string | null,
): Message {
  if (!timestamp || getMessageTimestamp(message)) {
    return message;
  }

  return {
    ...message,
    additional_kwargs: {
      ...(message.additional_kwargs ?? {}),
      timestamp,
    },
  } as Message;
}

/**
 * Propagate timestamps from source messages to target messages.
 *
 * Matches by identity (id/tool_call_id) first.  For human messages without
 * identity (run-event copies whose `id` is null), falls back to text matching
 * with OCCURRENCE ORDER: the 1st occurrence of a text in source maps to the
 * 1st occurrence in target, 2nd to 2nd, etc.  This correctly handles the
 * case where the user asks the same question twice — each copy gets its own
 * timestamp instead of one overwriting the other.
 */
export function mergeMissingTimestamps(
  sourceMessages: Message[],
  targetMessages: Message[],
): Message[] {
  const timestampByIdentity = new Map<string, string>();
  // For human messages without identity, match by text + occurrence order.
  // Key: `${text}#${occurrenceIndex}` (0-based)
  const timestampByTextAndOrder = new Map<string, string>();
  const sourceTextCounters = new Map<string, number>();

  for (const message of sourceMessages) {
    const identity = messageIdentity(message);
    const timestamp = getMessageTimestamp(message);
    if (identity && timestamp) {
      timestampByIdentity.set(identity, timestamp);
    }
    if (message.type === "human") {
      const text = normalizeHumanMessageText(message);
      if (text) {
        // Count EVERY occurrence (even without timestamp) so that
        // occurrence indices stay aligned between source and target.
        const count = sourceTextCounters.get(text) ?? 0;
        sourceTextCounters.set(text, count + 1);
        if (timestamp) {
          timestampByTextAndOrder.set(`${text}#${count}`, timestamp);
        }
      }
    }
  }

  if (timestampByIdentity.size === 0 && timestampByTextAndOrder.size === 0) {
    return targetMessages;
  }

  const targetTextCounters = new Map<string, number>();

  return targetMessages.map((message) => {
    const identity = messageIdentity(message);
    if (identity && timestampByIdentity.has(identity)) {
      return withMessageTimestamp(message, timestampByIdentity.get(identity));
    }
    // Fallback: match by text + occurrence order for human messages
    // whose identity is missing (run-event copies with null id).
    if (message.type === "human") {
      const text = normalizeHumanMessageText(message);
      if (text) {
        const count = targetTextCounters.get(text) ?? 0;
        targetTextCounters.set(text, count + 1);
        const key = `${text}#${count}`;
        if (timestampByTextAndOrder.has(key)) {
          return withMessageTimestamp(message, timestampByTextAndOrder.get(key));
        }
        // Debug-only: a missing match is expected when history hasn't
        // finished loading yet (early renders). Logging it as `error`
        // triggers the Next.js dev error overlay, so use `debug` instead.
        if (typeof window !== "undefined") {
          console.debug(
            "[mergeMissingTimestamps] FAIL text match",
            "text=" + JSON.stringify(text),
            "key=" + key,
            "id=" + (message.id ?? "null"),
            "content=" + JSON.stringify(message.content).slice(0, 200),
            "availableKeys=" + JSON.stringify(Array.from(timestampByTextAndOrder.keys())),
          );
        }
      }
    }
    return message;
  });
}
