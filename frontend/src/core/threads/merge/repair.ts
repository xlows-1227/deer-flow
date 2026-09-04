import type { Message } from "@langchain/langgraph-sdk";

import {
  extractTextFromMessage,
  getMessageTimestamp,
  repairDynamicContextUserMessageOrder,
} from "../../messages/utils";
import {
  dedupeAdjacentHumanByText,
  dedupeMessagesByIdentity,
  findLastMessageIndex,
} from "./identity";

export function messageIsAssistantSide(message: Message): boolean {
  return message.type === "ai" || message.type === "tool";
}

/**
 * During streaming, the backend can temporarily expose the current turn as
 * [AI/tool..., human]. Only repair that narrow tail shape. Historical slices
 * may contain multiple user turns; moving the first human there scrambles the
 * conversation.
 */
export function moveSingleTrailingHumanInputToFront(messages: Message[]): Message[] {
  const humanIndexes = messages.flatMap((message, index) =>
    message.type === "human" ? [index] : [],
  );
  if (humanIndexes.length !== 1) {
    return messages;
  }
  const firstHumanIndex = humanIndexes[0]!;
  if (
    firstHumanIndex <= 0 ||
    !messages.slice(0, firstHumanIndex).every(messageIsAssistantSide)
  ) {
    return messages;
  }

  const human = messages[firstHumanIndex]!;
  return [
    human,
    ...messages.slice(0, firstHumanIndex),
    ...messages.slice(firstHumanIndex + 1),
  ];
}

/**
 * When history has not caught up, thread.messages may already contain
 * completed prior turns plus the current in-flight tail. If a second
 * assistant message appears after an earlier completed reply, treat only
 * the trailing block as the new turn so optimistic input stays after prior
 * turns.
 */
export function splitThreadForOptimisticHuman(messages: Message[]): {
  established: Message[];
  currentTail: Message[];
} {
  const lastAiIndex = findLastMessageIndex(messages, messageIsAssistantSide);
  if (lastAiIndex === -1) {
    return { established: messages, currentTail: [] };
  }

  const priorAiIndex = findLastMessageIndex(
    messages,
    (message) => message.type === "ai",
    lastAiIndex,
  );

  if (priorAiIndex === -1) {
    if (messages.length === 1) {
      return {
        established: [],
        currentTail: moveSingleTrailingHumanInputToFront(messages),
      };
    }
    return { established: messages, currentTail: [] };
  }

  const trailingStart = priorAiIndex + 1;
  return {
    established: messages.slice(0, trailingStart),
    currentTail: moveSingleTrailingHumanInputToFront(
      messages.slice(trailingStart),
    ),
  };
}

/**
 * Repair trailing turn order by splitting established turns from the
 * in-flight tail.
 */
export function repairTrailingTurnOrder(messages: Message[]): Message[] {
  const { established, currentTail } = splitThreadForOptimisticHuman(messages);
  if (currentTail.length === 0) {
    return messages;
  }
  return [...established, ...currentTail];
}

/**
 * Extract the sequence number from a message.
 *
 * Checks `seq` (top-level), `response_metadata.seq`, and
 * `additional_kwargs.seq` in order.
 */
export function getMessageSeq(message: Message): number {
  const seq = (message as unknown as { seq?: unknown }).seq;
  if (typeof seq === "number" && Number.isFinite(seq)) return seq;
  const metadataSeq = (message as unknown as { response_metadata?: { seq?: unknown } })
    .response_metadata?.seq;
  if (typeof metadataSeq === "number" && Number.isFinite(metadataSeq))
    return metadataSeq;
  const additionalSeq = (message as unknown as { additional_kwargs?: { seq?: unknown } })
    .additional_kwargs?.seq;
  if (typeof additionalSeq === "number" && Number.isFinite(additionalSeq))
    return additionalSeq;
  return -Infinity;
}

/**
 * Check if a message is an optimistic UI placeholder (id starts with "opt-").
 */
export function isOptimisticMessage(message: Message): boolean {
  const id = message.id || "";
  return id.startsWith("opt-");
}

/**
 * Stable, last-resort ordering for merged messages.
 *
 * Sorts by:
 *   1. `seq` (backend-assigned per-message ordering) if available
 *   2. parsed timestamp for messages that carry one
 *   3. original array index as a tiebreaker so the sort remains stable
 */
export function sortMessagesByTime(messages: Message[]): Message[] {
  if (messages.length <= 1) return messages;

  const optimisticMsgs: Message[] = [];
  const serverMsgs: Message[] = [];
  const optimisticIndices: number[] = [];

  messages.forEach((m, index) => {
    if (isOptimisticMessage(m)) {
      optimisticMsgs.push(m);
      optimisticIndices.push(index);
    } else {
      serverMsgs.push(m);
    }
  });

  const sortedServerMsgs = serverMsgs.map((m, index) => ({ m, index }));
  sortedServerMsgs.sort((a, b) => {
    // Only sort by timestamp when BOTH messages have one.
    const tsA = getMessageTimestamp(a.m);
    const tsB = getMessageTimestamp(b.m);
    if (tsA && tsB) {
      const cmp = tsA.localeCompare(tsB);
      if (cmp !== 0) return cmp;
    }

    // 第二优先级：seq（后端分配的同一 run 内的序号）
    const seqA = getMessageSeq(a.m);
    const seqB = getMessageSeq(b.m);
    if (Number.isFinite(seqA) && Number.isFinite(seqB) && seqA !== seqB) {
      return seqA - seqB;
    }

    // 第三优先级：原数组索引（稳定排序）
    return a.index - b.index;
  });

  if (optimisticMsgs.length === 0) {
    return sortedServerMsgs.map(({ m }) => m);
  }

  const result: Message[] = [];
  let optIdx = 0;

  for (const serverMsg of sortedServerMsgs) {
    const origIdx = messages.indexOf(serverMsg.m);
    while (optIdx < optimisticMsgs.length) {
      const optOrigIdx = optimisticIndices[optIdx]!;
      if (optOrigIdx < origIdx) {
        result.push(optimisticMsgs[optIdx]!);
        optIdx++;
      } else {
        break;
      }
    }
    result.push(serverMsg.m);
  }

  while (optIdx < optimisticMsgs.length) {
    result.push(optimisticMsgs[optIdx]!);
    optIdx++;
  }

  return result;
}

/**
 * Final cleanup pass after merging: deduplicate by identity, remove adjacent
 * text-duplicate human messages, and repair trailing turn order.
 *
 * `repairDynamicContextUserMessageOrder` is only called when
 * `hasHistoryMerge` is false (streaming-only case).  When history is
 * prepended and thread follows, `dedupeMessagesByIdentity` replaces
 * history's `__user` copies with thread's copies at the HISTORY position
 * (earlier), while the reminders stay at the THREAD position (later).
 * `repairDynamicContextUserMessageOrder` would then move `__user` copies
 * to follow their reminders — pulling them to the end and stratifying
 * the conversation into "answers first, questions last".
 *
 * NOTE: `sortMessagesByTime` is intentionally NOT called here — the merge
 * logic already preserves correct chronological order (history → thread →
 * optimistic).  The timestamp sort was causing Q1,Q2,Q3,Q4,A1,A2 ordering
 * when AI messages lacked timestamps but human messages had them.
 */
export function finalizeMergedMessages(
  messages: Message[],
  hasHistoryMerge = false,
): Message[] {
  const deduped = dedupeMessagesByIdentity(messages);
  const textDeduped = dedupeAdjacentHumanByText(deduped);
  const turnRepaired = repairTrailingTurnOrder(textDeduped);
  const repaired = hasHistoryMerge
    ? turnRepaired
    : repairDynamicContextUserMessageOrder(turnRepaired);

  // DEBUG: log timestamp state to diagnose "time changes on refresh"
  if (typeof window !== "undefined") {
    const tsSummary = repaired.map((m, i) => ({
      idx: i,
      type: m.type,
      id: m.id ?? (m as Record<string, unknown>).tool_call_id ?? "no-id",
      ts: getMessageTimestamp(m),
      hasResponseTs: !!m.response_metadata?.created_at,
      hasKwargsTs: !!m.additional_kwargs?.timestamp,
      aiText: m.type === "ai" ? extractTextFromMessage(m).slice(0, 30) : undefined,
    }));
    console.groupCollapsed(
      "[mergeMessages DEBUG] final messages timestamp check",
    );
    console.table(tsSummary);
    console.groupEnd();
  }

  return repaired;
}
