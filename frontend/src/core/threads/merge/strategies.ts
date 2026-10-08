import type { Message } from "@langchain/langgraph-sdk";

import { findLastMessageIndex } from "./identity";
import {
  firstEquivalentIndex,
  isAlignmentNoiseMessage,
  lastEquivalentIndex,
} from "./overlap";
import { moveSingleTrailingHumanInputToFront } from "./repair";

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
  isLoading: boolean = true,
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
  // Thread-only extras — e.g. the ask_clarification ToolMessage injected
  // into checkpoint state via Command(goto=END), which never produces a run
  // event — must keep their checkpoint position. Appending them after the
  // whole history displaces them to the very end of the conversation, so
  // each extra is anchored right after the history message corresponding to
  // its nearest preceding matched thread message. Alignment noise such as
  // DynamicContext reminders is dropped: it is injected into checkpoint
  // state but never appears in run-event history, so it always lands in the
  // extras bucket and would otherwise re-appear at every anchor.
  const extrasByHistoryIndex = new Map<number, Message[]>();
  const extrasBeforeHistory: Message[] = [];
  let anchorHistoryIndex = -1;
  for (let index = firstMatch; index < threadMessages.length; index += 1) {
    const message = threadMessages[index]!;
    if (isAlignmentNoiseMessage(message)) {
      continue;
    }
    const matchedHistoryIndex = firstEquivalentIndex(historyMessages, message);
    if (matchedHistoryIndex >= 0) {
      anchorHistoryIndex = matchedHistoryIndex;
      continue;
    }
    if (anchorHistoryIndex === -1) {
      extrasBeforeHistory.push(message);
    } else {
      const bucket = extrasByHistoryIndex.get(anchorHistoryIndex) ?? [];
      bucket.push(message);
      extrasByHistoryIndex.set(anchorHistoryIndex, bucket);
    }
  }

  const tail: Message[] = [...extrasBeforeHistory];
  historyMessages.forEach((message, index) => {
    tail.push(message);
    const extras = extrasByHistoryIndex.get(index);
    if (extras && extras.length > 0) {
      tail.push(...extras);
    }
  });

  return mergeThreadAndOptimisticMessages(
    prefix,
    tail,
    optimisticMessages,
    isLoading,
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
 * When there IS a human optimistic message, the optimistic human is
 * appended to the END of the merged array. Reasoning: opt-human exists
 * only in the brief window after the user submits but before the server
 * echoes back the real human message (visibleOptimisticMessages filters
 * out opt-human once the server copy arrives). During that window the
 * LLM cannot have already started generating a new AI reply — it has
 * not received the new human input yet — so thread.messages contains
 * only completed prior turns. Any AI in thread.messages is therefore a
 * PRIOR turn's already-completed AI, not an in-flight streaming AI.
 * The previous logic (splitThreadForOptimisticHuman) mis-classified
 * that prior-completed AI as in-flight and inserted opt-human before
 * it, producing the "new question covers the previous Q" misordering.
 */
export function mergeThreadAndOptimisticMessages(
  establishedThreadPrefix: Message[],
  threadNewSegment: Message[],
  optimisticMessages: Message[],
  isLoading: boolean = true,
): Message[] {
  const humanOptimistic = optimisticMessages.filter(
    (message) => message.type === "human",
  );
  const otherOptimistic = optimisticMessages.filter(
    (message) => message.type !== "human",
  );

  if (humanOptimistic.length === 0) {
    // 当 threadNewSegment 包含 tool 消息时，说明上一轮 AI 正在调用工具
    //（in-flight 状态）。此时 segment 末尾的 human 是用户刚提交的新问题，
    // 不是"流式边缘 case"中错位的输入。不能调用 moveSingleTrailingHumanInputToFront
    // 把它移到 tool/AI 前面，否则会造成"新Q替换上一条Q"的视觉错位。
    const hasInFlightTools = threadNewSegment.some(
      (m) => m.type === "tool",
    );
    const currentTurnTail = hasInFlightTools
      ? threadNewSegment
      : moveSingleTrailingHumanInputToFront(threadNewSegment);
    return [...establishedThreadPrefix, ...currentTurnTail, ...otherOptimistic];
  }

  // opt-human 存在 ⟹ 服务器还没回显新 human ⟹ LLM 不可能已在生成新 AI ⟹
  // thread.messages 里没有新一轮 in-flight AI ⟹ opt-human 直接 append 到末尾。
  // 不再调用 splitThreadForOptimisticHuman（它会把上一轮已完成 AI 误判为
  // in-flight tail）。`isLoading` 参数仍下传以保留调用链签名兼容，但当前
  // 实现不再依赖它。
  void isLoading;
  return [
    ...establishedThreadPrefix,
    ...threadNewSegment,
    ...humanOptimistic,
    ...otherOptimistic,
  ];
}
