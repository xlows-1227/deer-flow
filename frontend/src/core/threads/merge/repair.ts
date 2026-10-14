import type { Message } from "@langchain/langgraph-sdk";

import {
  extractTextFromMessage,
  getMessageTimestamp,
  isClarificationToolMessage,
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
 *
 * A human that directly follows a clarification card (ask_clarification
 * ToolMessage) is the user's ANSWER to that card, not a misordered streaming
 * input — the card must stay immediately before its answer.
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
  const predecessor = messages[firstHumanIndex - 1]!;
  if (isClarificationToolMessage(predecessor)) {
    return messages;
  }

  const human = messages[firstHumanIndex]!;
  // opt-human 是用户刚提交的新问题占位符，必须留在末尾（在上一轮 in-flight
  // tool/AI 之后）。如果把它移到前面，会插到上一轮正在生成的 tool/AI 消息之前，
  // 导致"新Q替换上一条Q"的视觉错位。
  if (isOptimisticMessage(human)) {
    return messages;
  }

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
 *
 * `isLoading` 短路：当 `thread.isLoading === false`（上一轮已结束、本轮
 * 流式尚未启动）时，整个 `thread.messages` 都是已完成 turn，没有
 * in-flight tail —— opt-human 应直接 append 到末尾，而不是被插入到
 * 上一轮的最后一个 AI 之前。否则会触发"新提问先在上一个回答之上"
 * 的临时错位。
 */
export function splitThreadForOptimisticHuman(
  messages: Message[],
  isLoading: boolean = true,
): {
  established: Message[];
  currentTail: Message[];
} {
  if (!isLoading) {
    return { established: messages, currentTail: [] };
  }
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
 * Only reorders pairs that are CLEARLY out of chronological order:
 *
 *   1. Both carry a timestamp AND are more than 5 seconds apart — this
 *      catches real cross-turn misalignment (e.g. overlap detection that
 *      mixed up Q1A1 with Q2A2).
 *   2. Otherwise falls through to `seq` then original array index, which
 *      preserves the correct intra-turn order (Human → AI(tool_calls) →
 *      ToolMessage → AI follow-up).  ToolMessage timestamps are often 1-2
 *      ms earlier than the AIMessage that invoked them, so strict
 *      timestamp sorting would flip them and break tool-call flow.
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

  // Pre-parse timestamps to ms once per message (avoid repeated Date.parse).
  const parsedTsCache = new Map<number, number | null>();
  const getTsMs = (m: Message, idx: number): number | null => {
    if (parsedTsCache.has(idx)) return parsedTsCache.get(idx) ?? null;
    // A DynamicContext ``__user`` copy is stamped by the middleware when the
    // reminder is injected — not when the user typed. Its real position is
    // anchored to the base message it replaced, so that stamp must never be
    // used to reorder it (doing so stranded the first user message in the
    // middle of the conversation). Fall through to seq/index instead.
    const isDynamicContextCopy =
      m.type === "human" &&
      typeof m.id === "string" &&
      m.id.endsWith("__user");
    if (isDynamicContextCopy) {
      parsedTsCache.set(idx, null);
      return null;
    }
    const ts = getMessageTimestamp(m);
    if (!ts) {
      parsedTsCache.set(idx, null);
      return null;
    }
    const ms = Date.parse(ts);
    const val = Number.isFinite(ms) ? ms : null;
    parsedTsCache.set(idx, val);
    return val;
  };

  const TURN_GUARD_MS = 5_000; // 5s — intra-turn messages stay in original order

  const sortedServerMsgs = serverMsgs.map((m, index) => ({ m, index }));
  sortedServerMsgs.sort((a, b) => {
    const tsA = getTsMs(a.m, a.index);
    const tsB = getTsMs(b.m, b.index);
    // Only timestamp-compare when BOTH have a parseable timestamp AND
    // they are sufficiently far apart (>5s) to be across turns.
    if (tsA !== null && tsB !== null && Math.abs(tsA - tsB) > TURN_GUARD_MS) {
      return tsA - tsB; // ascending chronological: older (smaller ts) first
    }

    // Second priority: seq (backend-assigned run-internal ordering).
    const seqA = getMessageSeq(a.m);
    const seqB = getMessageSeq(b.m);
    if (Number.isFinite(seqA) && Number.isFinite(seqB) && seqA !== seqB) {
      return seqA - seqB;
    }

    // Final tiebreaker: original array index (stable, preserves input order).
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
 * text-duplicate human messages, repair trailing turn order, then sort by
 * timestamp as a last-resort chronological safety net.
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
 * `sortMessagesByTime` is called LAST (after dedupe + repair) because it
 * only reorders pairs where BOTH sides carry a timestamp — messages still
 * missing a timestamp fall through to the stable original-index tiebreaker,
 * so this does not displace newly-injected middleware messages or optimistic
 * placeholders.  Previously disabled because AI messages lacked timestamps
 * entirely, which caused the "Q1Q2Q3Q4A1A2" stratification bug when humans
 * had timestamps but AI did not.  With the backend fix (every AIMessage
 * now carries `additional_kwargs.timestamp` from the moment it enters the
 * system), the sort correctly restores Q1A1Q2A2 ordering even when the
 * merge logic's overlap detection misaligns.
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

  // Last-resort chronological sort — safe now that every message carries a
  // backend-assigned timestamp.  Falls back to seq then original index for
  // pairs where one or both sides still lack a timestamp (e.g. optimistic
  // placeholders, older messages from before the backend fix).
  return sortMessagesByTime(repaired);
}
