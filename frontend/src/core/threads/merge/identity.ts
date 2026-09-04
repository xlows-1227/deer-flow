import type { Message } from "@langchain/langgraph-sdk";

import {
  extractTextFromMessage,
  getMessageTimestamp,
  parseUploadedFiles,
  stripUploadedFilesTag,
  type FileInMessage,
} from "../../messages/utils";

/**
 * Tolerance window for timestamp comparison (5 minutes).
 *
 * History messages (run-event copies) get timestamps from ``run.created_at``
 * or event ``created_at``, while thread messages (checkpoint copies) get
 * timestamps backfilled from adjacent AI message timestamps.  The same
 * logical message therefore carries different timestamps in the two
 * sources (differing by seconds to a minute).  An exact ``ts === ts``
 * comparison causes ``messagesEquivalent`` to return false, which breaks
 * overlap detection and pushes messages to the bottom after "load more".
 *
 * 5 minutes is wide enough to absorb run-vs-checkpoint timestamp drift
 * but narrow enough to distinguish two genuinely different user questions
 * asked far apart.
 */
const TIMESTAMP_TOLERANCE_MS = 5 * 60 * 1000;

/**
 * Compare two timestamps with a tolerance window.
 *
 * - If either timestamp is null/empty, returns ``true`` (fall back to
 *   text-only matching — the positional overlap logic handles duplicates).
 * - If both are parseable, returns ``true`` when they are within
 *   ``TIMESTAMP_TOLERANCE_MS`` of each other.
 * - If either is unparseable, returns ``true`` (can't distinguish).
 */
export function timestampsAreClose(
  tsA: string | null | undefined,
  tsB: string | null | undefined,
): boolean {
  if (!tsA || !tsB) return true;
  const msA = Date.parse(tsA);
  const msB = Date.parse(tsB);
  if (!Number.isFinite(msA) || !Number.isFinite(msB)) return true;
  return Math.abs(msA - msB) <= TIMESTAMP_TOLERANCE_MS;
}

/**
 * Build a stable identity key for a message, used for deduplication.
 *
 * Prefers `tool_call_id` for tool messages, then `message.id` for regular
 * messages.  Returns `undefined` when no stable identity is available
 * (e.g. run-event human messages whose `id` is null).
 *
 * DynamicContextMiddleware splits each human message into a hidden
 * reminder (original id, e.g. `abc`) and a visible `__user` copy
 * (`abc__user`).  Without stripping the `__user` suffix, dedupe treats
 * them as two distinct messages — both appear in the merged result, the
 * original (from history, no timestamp) sits at an earlier position while
 * the `__user` copy (from thread, with timestamp) lands elsewhere,
 * producing duplicate Q and Q misordering ("没时间的Q被落下来").
 * Stripping `__user` for human messages makes both versions share the
 * same identity so dedupe keeps only the later (thread) copy.
 */
export function messageIdentity(message: Message): string | undefined {
  if (
    "tool_call_id" in message &&
    typeof message.tool_call_id === "string" &&
    message.tool_call_id.length > 0
  ) {
    return `tool:${message.tool_call_id}`;
  }
  if (typeof message.id === "string" && message.id.length > 0) {
    // 对 human 消息去掉 __user 后缀，使原始（id=X）和 __user copy
    // （id=X__user）共享 identity，dedupe 正确去重。
    if (message.type === "human" && message.id.endsWith("__user")) {
      return `message:${message.id.slice(0, -"__user".length)}`;
    }
    return `message:${message.id}`;
  }
  return undefined;
}

/**
 * Extract normalized text from a human message (strips uploaded-files tags).
 */
export function normalizeHumanMessageText(message: Message): string {
  if (message.type !== "human") {
    return "";
  }
  return stripUploadedFilesTag(extractTextFromMessage(message)).trim();
}

/**
 * Check if a history message and a thread message are equivalent.
 *
 * Matches by identity (id/tool_call_id) first, then by text content for
 * human and AI messages.  This handles the DynamicContextMiddleware rename
 * (e.g. `msg-1` → `msg-1__user`) where IDs differ but content matches.
 */
export function messagesEquivalent(
  historyMessage: Message,
  threadMessage: Message,
): boolean {
  const historyId = messageIdentity(historyMessage);
  const threadId = messageIdentity(threadMessage);
  if (historyId && threadId && historyId === threadId) {
    return true;
  }
  if (historyMessage.type !== threadMessage.type) {
    return false;
  }
  if (historyMessage.type === "human") {
    const historyText = normalizeHumanMessageText(historyMessage);
    const threadText = normalizeHumanMessageText(threadMessage);
    if (historyText.length === 0 || historyText !== threadText) {
      return false;
    }
    // Same text. Compare timestamps with a tolerance window to handle
    // the case where history (run-event) and thread (checkpoint) copies
    // of the same message carry slightly different timestamps.  Only
    // treat them as different when both have parseable timestamps that
    // are more than 5 minutes apart — indicating genuinely different
    // user questions (asked the same thing much later).
    const historyTs = getMessageTimestamp(historyMessage) ?? null;
    const threadTs = getMessageTimestamp(threadMessage) ?? null;
    if (!timestampsAreClose(historyTs, threadTs)) {
      return false;
    }
    return true;
  }
  if (historyMessage.type === "ai") {
    const historyText = extractTextFromMessage(historyMessage).trim();
    const threadText = extractTextFromMessage(threadMessage).trim();
    return historyText.length > 0 && historyText === threadText;
  }
  return false;
}

/**
 * Deduplicate messages by identity (id/tool_call_id).
 *
 * Always prefers the LATER copy for each identity — the thread (checkpoint)
 * layer appears after history in the merged array, and checkpoint messages
 * have the final/full AI content.
 */
export function dedupeMessagesByIdentity(messages: Message[]): Message[] {
  const bestIndexByIdentity = new Map<string, number>();

  messages.forEach((message, index) => {
    const identity = messageIdentity(message);
    if (identity) {
      bestIndexByIdentity.set(identity, index);
    }
  });

  const emittedIdentities = new Set<string>();
  const result: Message[] = [];
  for (let index = 0; index < messages.length; index++) {
    const message = messages[index]!;
    const primaryIdentity = messageIdentity(message);

    if (!primaryIdentity) {
      result.push(message);
      continue;
    }
    if (emittedIdentities.has(primaryIdentity)) {
      continue;
    }
    const resolvedIdentity = primaryIdentity;
    if (!bestIndexByIdentity.has(resolvedIdentity)) {
      result.push(message);
      emittedIdentities.add(primaryIdentity);
      continue;
    }
    emittedIdentities.add(resolvedIdentity);
    const chosenIdx = bestIndexByIdentity.get(resolvedIdentity)!;
    const chosenMsg = messages[chosenIdx]!;
    const chosenHasTs = getMessageTimestamp(chosenMsg) !== null;
    result.push(chosenMsg);
    if (typeof window !== "undefined") {
      console.debug(
        "[dedupe] chose idx",
        chosenIdx,
        "for identity",
        resolvedIdentity,
        "type=",
        chosenMsg.type,
        "hasTs=",
        chosenHasTs,
        "ts=",
        getMessageTimestamp(chosenMsg)?.slice(0, 19) ?? "null",
        "id=",
        chosenMsg.id,
      );
    }
  }
  return result;
}

/**
 * Safety-net text dedup for human messages that slip through identity-based
 * dedup because DynamicContextMiddleware renamed their IDs (e.g. msg-1 →
 * msg-1__user).  Only removes a later copy when the SAME normalized text
 * appears within a small window (≤3 messages apart) — this catches
 * overlapping history/thread copies that land adjacent after merge without
 * removing legitimately repeated user questions that are far apart.
 */
export function dedupeAdjacentHumanByText(messages: Message[]): Message[] {
  if (messages.length <= 1) return messages;
  const result: Message[] = [];
  const recentTexts: string[] = [];

  for (const m of messages) {
    if (m.type === "human") {
      const text = normalizeHumanMessageText(m);
      if (text && recentTexts.includes(text)) {
        continue; // skip adjacent duplicate
      }
      recentTexts.push(text ?? "");
      if (recentTexts.length > 3) recentTexts.shift();
    } else {
      recentTexts.length = 0;
    }
    result.push(m);
  }
  return result;
}

/**
 * Build a stable signature for the files attached to a human message.
 *
 * The optimistic UI stores files on `message.additional_kwargs.files[]` (or
 * `message.files[]` for backwards compat).  The server echoes them back via
 * a `<uploaded_files>…</uploaded_files>` marker inside `message.content`
 * **and** also populates `additional_kwargs.files[]` on the echoed message
 * when re-serialized from checkpoint/history.
 */
export function humanMessageFilesSignature(message: Message): string | null {
  if (message.type !== "human") return null;
  const rawFiles: FileInMessage[] = [];
  const fromAdditional = (
    message as unknown as { additional_kwargs?: { files?: FileInMessage[] } }
  ).additional_kwargs?.files;
  if (Array.isArray(fromAdditional)) rawFiles.push(...fromAdditional);
  const fromDirect = (message as unknown as { files?: FileInMessage[] }).files;
  if (Array.isArray(fromDirect)) rawFiles.push(...fromDirect);
  const textContent = extractTextFromMessage(message);
  if (typeof textContent === "string" && textContent.includes("<uploaded_files>")) {
    rawFiles.push(...parseUploadedFiles(textContent));
  }
  if (rawFiles.length === 0) return null;
  const signature = rawFiles
    .map((f) => {
      const name = f.filename ?? "";
      const size = typeof f.size === "number" ? String(f.size) : "";
      const path = f.path ?? "";
      return `${name}|${size}|${path}`;
    })
    .sort()
    .join(";");
  return `human-files:${signature}`;
}

/**
 * Build a visibility key for a human message, used to match optimistic
 * messages against server echoes.
 */
export function humanMessageVisibilityKey(message: Message): string | null {
  if (message.type !== "human") {
    return null;
  }
  const identity = messageIdentity(message);
  if (identity) {
    return identity;
  }
  const text = normalizeHumanMessageText(message);
  if (text) return `human-content:${text}`;
  // Pure-file sends (no text) fall back to a file-based signature so
  // server echo can still be matched against the optimistic message.
  return humanMessageFilesSignature(message);
}

/**
 * Collect all visibility keys from a set of messages.
 */
export function getHumanMessageVisibilityKeys(messages: Message[]): Set<string> {
  const keys = new Set<string>();
  for (const message of messages) {
    const key = humanMessageVisibilityKey(message);
    if (key) {
      keys.add(key);
    }
  }
  return keys;
}

/**
 * Find the last index of a message matching a predicate, searching backwards
 * from `beforeIndex`.
 */
export function findLastMessageIndex(
  messages: Message[],
  predicate: (message: Message) => boolean,
  beforeIndex = messages.length,
): number {
  for (
    let index = Math.min(beforeIndex, messages.length) - 1;
    index >= 0;
    index--
  ) {
    const message = messages[index];
    if (message && predicate(message)) {
      return index;
    }
  }
  return -1;
}
