import type { AIMessage, Message } from "@langchain/langgraph-sdk";

import { THREAD_SOURCE_SCHEDULED_TASK } from "@/core/threads/utils";

/**
 * Check if a message is an AI-type message.
 * Runtime data may include "ai", "assistant", "AIMessage", or "AIMessageChunk"
 * depending on the LangChain/LangGraph version and serialization path.
 */
export function isAiMessage(message: Message | { type?: string } | null | undefined): boolean {
  if (!message) return false;
  const t = (message as { type?: string }).type;
  return t === "ai" || t === "assistant" || t === "AIMessage" || t === "AIMessageChunk";
}

/**
 * Safely get tool_calls from a message that has been confirmed as an AI message.
 */
export function getToolCalls(message: Message): Array<{ name: string; args: Record<string, unknown>; id?: string }> {
  return ((message as unknown as { tool_calls?: Array<{ name: string; args: Record<string, unknown>; id?: string }> }).tool_calls ?? []);
}

interface GenericMessageGroup<T = string> {
  type: T;
  id: string | undefined;
  messages: Message[];
}

interface HumanMessageGroup extends GenericMessageGroup<"human"> {}

interface AssistantProcessingGroup extends GenericMessageGroup<"assistant:processing"> {}

interface AssistantMessageGroup extends GenericMessageGroup<"assistant"> {}

interface AssistantPresentFilesGroup extends GenericMessageGroup<"assistant:present-files"> {}

interface AssistantClarificationGroup extends GenericMessageGroup<"assistant:clarification"> {}

interface AssistantSubagentGroup extends GenericMessageGroup<"assistant:subagent"> {}

export type MessageGroup =
  | HumanMessageGroup
  | AssistantProcessingGroup
  | AssistantMessageGroup
  | AssistantPresentFilesGroup
  | AssistantClarificationGroup
  | AssistantSubagentGroup;

const HIDDEN_CONTROL_MESSAGE_NAMES = new Set([
  "summary",
  "loop_warning",
  "todo_reminder",
  "todo_completion_reminder",
]);

export function getMessageGroups(messages: Message[]): MessageGroup[] {
  if (messages.length === 0) {
    return [];
  }

  const groups: MessageGroup[] = [];

  function isOpenToolGroup(group: MessageGroup) {
    return (
      group.type !== "human" &&
      group.type !== "assistant" &&
      group.type !== "assistant:clarification"
    );
  }

  // Returns the most recent group that can still accept tool messages
  // (i.e. an in-flight processing/subagent/present-files group).
  function lastOpenGroup() {
    for (let i = groups.length - 1; i >= 0; i--) {
      const group = groups[i];
      if (group && isOpenToolGroup(group)) {
        return group;
      }
    }
    return null;
  }

  function findGroupForToolCallId(toolCallId: string) {
    for (let i = groups.length - 1; i >= 0; i--) {
      const group = groups[i];
      if (!group) {
        continue;
      }
      for (const groupMessage of group.messages) {
        if (
          isAiMessage(groupMessage) &&
          getToolCalls(groupMessage).some(
            (toolCall) => toolCall.id === toolCallId,
          )
        ) {
          return group;
        }
      }
    }
    return null;
  }

  function findGroupForToolMessage(message: Message) {
    if (message.type !== "tool") {
      return null;
    }

    if (message.tool_call_id) {
      const matchedGroup = findGroupForToolCallId(message.tool_call_id);
      if (matchedGroup) {
        return matchedGroup;
      }
    }

    return lastOpenGroup();
  }

  function isIncompleteToolMessage(message: Message) {
    return (
      message.type === "tool" &&
      !message.tool_call_id &&
      typeof message.name !== "string" &&
      extractTextFromMessage(message).length === 0
    );
  }

  for (const message of messages) {
    if (isHiddenFromUIMessage(message)) {
      continue;
    }

    if (message.type === "human") {
      groups.push({ id: message.id, type: "human", messages: [message] });
      continue;
    }

    if (message.type === "tool") {
      if (isIncompleteToolMessage(message)) {
        continue;
      }

      if (isClarificationToolMessage(message)) {
        // Add to the processing group that owns the tool call, then also open a
        // standalone clarification group for prominent display.
        findGroupForToolMessage(message)?.messages.push(message);
        groups.push({
          id: message.id,
          type: "assistant:clarification",
          messages: [message],
        });
      } else {
        const targetGroup = findGroupForToolMessage(message);
        if (targetGroup) {
          targetGroup.messages.push(message);
        }
      }
      continue;
    }

    if (isAiMessage(message)) {
      if (hasPresentFiles(message)) {
        groups.push({
          id: message.id,
          type: "assistant:present-files",
          messages: [message],
        });
      } else if (hasSubagent(message)) {
        groups.push({
          id: message.id,
          type: "assistant:subagent",
          messages: [message],
        });
      } else if (hasReasoning(message) || hasToolCalls(message)) {
        const lastGroup = groups[groups.length - 1];
        // Accumulate consecutive intermediate AI messages into one processing group.
        if (lastGroup?.type !== "assistant:processing") {
          groups.push({
            id: message.id,
            type: "assistant:processing",
            messages: [message],
          });
        } else {
          lastGroup.messages.push(message);
        }
        // Message already placed in a processing group — do NOT also add it to
        // the standalone assistant group below, otherwise the same content will
        // appear twice (once via the processing group's MarkdownContent and once
        // via the assistant group's MessageListItem).
        continue;
      }

      // Only add to the assistant group when the message was NOT already
      // consumed by a processing group above.
      if (hasContent(message) && !hasToolCalls(message)) {
        groups.push({ id: message.id, type: "assistant", messages: [message] });
      }
    }
  }

  return groups;
}

export function getMessageGroupRenderKey(
  group: MessageGroup,
  groupIndex: number,
): string {
  const id =
    typeof group.id === "string" && group.id.length > 0 ? group.id : "no-id";
  return `${group.type}:${groupIndex}:${id}`;
}

export function getMessageRenderKey(
  group: MessageGroup,
  groupIndex: number,
  message: Message,
  messageIndex: number,
): string {
  const messageId =
    typeof message.id === "string" && message.id.length > 0
      ? message.id
      : `idx-${messageIndex}`;
  return `${getMessageGroupRenderKey(group, groupIndex)}/${messageId}`;
}

export function groupMessages<T>(
  messages: Message[],
  mapper: (group: MessageGroup) => T,
): T[] {
  return getMessageGroups(messages)
    .map(mapper)
    .filter((result) => result !== undefined && result !== null) as T[];
}

export function getAssistantTurnUsageMessages(groups: MessageGroup[]) {
  const usageMessagesByGroupIndex: Array<Message[] | null> = Array.from(
    { length: groups.length },
    () => null,
  );

  let turnStartIndex: number | null = null;

  for (const [index, group] of groups.entries()) {
    if (group.type === "human") {
      turnStartIndex = null;
      continue;
    }

    turnStartIndex ??= index;

    const nextGroup = groups[index + 1];
    const isTurnEnd = !nextGroup || nextGroup.type === "human";

    if (!isTurnEnd) {
      continue;
    }

    usageMessagesByGroupIndex[index] = groups
      .slice(turnStartIndex, index + 1)
      .flatMap((currentGroup) => currentGroup.messages)
      .filter((message) => isAiMessage(message));

    turnStartIndex = null;
  }

  return usageMessagesByGroupIndex;
}

type MessageMetadataLookup = (
  message: Message,
  index: number,
) => { streamMetadata?: Record<string, unknown> } | undefined;

export type StreamingMessageLookup = {
  ids: ReadonlySet<string>;
  messages: ReadonlySet<Message>;
};

export function getStreamingMessageLookup(
  messages: Message[],
  isStreaming: boolean,
  getMessagesMetadata?: MessageMetadataLookup,
): StreamingMessageLookup {
  const streamingMessageIds = new Set<string>();
  const streamingMessages = new Set<Message>();

  if (!isStreaming) {
    return {
      ids: streamingMessageIds,
      messages: streamingMessages,
    };
  }

  messages.forEach((message, index) => {
    if (!getMessagesMetadata?.(message, index)?.streamMetadata) {
      return;
    }

    if (typeof message.id === "string" && message.id.length > 0) {
      streamingMessageIds.add(message.id);
    }
    streamingMessages.add(message);
  });

  return {
    ids: streamingMessageIds,
    messages: streamingMessages,
  };
}

export function isAssistantMessageGroupStreaming(
  groupMessages: Message[],
  streamingMessages: StreamingMessageLookup,
) {
  return groupMessages.some((message) => {
    if (message.type !== "ai") {
      return false;
    }

    return (
      (typeof message.id === "string" &&
        message.id.length > 0 &&
        streamingMessages.ids.has(message.id)) ||
      streamingMessages.messages.has(message)
    );
  });
}

export function getAssistantTurnCopyData(
  messages: Message[],
  { isStreaming = false }: { isStreaming?: boolean } = {},
) {
  if (isStreaming) {
    return null;
  }

  return (
    [...messages]
      .reverse()
      .filter((message) => isAiMessage(message))
      .map((message) => {
        const content = extractContentFromMessage(message);
        return content ?? extractReasoningContentFromMessage(message) ?? "";
      })
      .find((content) => content.length > 0) ?? null
  );
}

export function extractTextFromMessage(message: Message) {
  if (typeof message.content === "string") {
    return (
      splitInlineReasoningFromAIMessage(message)?.content ??
      message.content.trim()
    );
  }
  if (Array.isArray(message.content)) {
    return message.content
      .map((content) => (content.type === "text" ? content.text : ""))
      .join("\n")
      .trim();
  }
  return "";
}

const THINK_TAG_RE = /<think>\s*([\s\S]*?)\s*<\/think>/g;

function splitInlineReasoning(content: string) {
  const reasoningParts: string[] = [];
  const cleaned = content
    .replace(THINK_TAG_RE, (_, reasoning: string) => {
      const normalized = reasoning.trim();
      if (normalized) {
        reasoningParts.push(normalized);
      }
      return "";
    })
    .trim();

  return {
    content: cleaned,
    reasoning: reasoningParts.length > 0 ? reasoningParts.join("\n\n") : null,
  };
}

function splitInlineReasoningFromAIMessage(message: Message) {
  if (!isAiMessage(message) || typeof message.content !== "string") {
    return null;
  }
  return splitInlineReasoning(message.content);
}

export function extractContentFromMessage(message: Message) {
  if (typeof message.content === "string") {
    return (
      splitInlineReasoningFromAIMessage(message)?.content ??
      message.content.trim()
    );
  }
  if (Array.isArray(message.content)) {
    return message.content
      .map((content) => {
        switch (content.type) {
          case "text":
            return content.text;
          case "image_url":
            const imageURL = extractURLFromImageURLContent(content.image_url);
            return `![image](${imageURL})`;
          default:
            return "";
        }
      })
      .join("\n")
      .trim();
  }
  return "";
}

export function extractReasoningContentFromMessage(message: Message) {
  if (!isAiMessage(message)) {
    return null;
  }
  if (
    message.additional_kwargs &&
    "reasoning_content" in message.additional_kwargs
  ) {
    return message.additional_kwargs.reasoning_content as string | null;
  }
  if (Array.isArray(message.content)) {
    const part = message.content[0];
    if (part && typeof part === "object" && "thinking" in part) {
      return part.thinking as string;
    }
  }
  if (typeof message.content === "string") {
    return splitInlineReasoning(message.content).reasoning;
  }
  return null;
}

export function removeReasoningContentFromMessage(message: Message) {
  if (message.type !== "ai" || !message.additional_kwargs) {
    return;
  }
  delete message.additional_kwargs.reasoning_content;
}

export function extractURLFromImageURLContent(
  content:
    | string
    | {
        url: string;
      },
) {
  if (typeof content === "string") {
    return content;
  }
  return content.url;
}

export function hasContent(message: Message) {
  if (typeof message.content === "string") {
    return (
      (
        splitInlineReasoningFromAIMessage(message)?.content ??
        message.content.trim()
      ).length > 0
    );
  }
  if (Array.isArray(message.content)) {
    return message.content.length > 0;
  }
  return false;
}

export function hasReasoning(message: Message) {
  if (!isAiMessage(message)) {
    return false;
  }
  if (typeof message.additional_kwargs?.reasoning_content === "string") {
    return true;
  }
  if (Array.isArray(message.content)) {
    const part = message.content[0];
    // Compatible with the Anthropic gateway
    return (part as unknown as { type: "thinking" })?.type === "thinking";
  }
  if (typeof message.content === "string") {
    return splitInlineReasoning(message.content).reasoning !== null;
  }
  return false;
}

export function hasToolCalls(message: Message) {
  return (
    isAiMessage(message) && getToolCalls(message).length > 0
  );
}

export function hasPresentFiles(message: Message) {
  return (
    isAiMessage(message) &&
    getToolCalls(message).some((toolCall) => toolCall.name === "present_files")
  );
}

export function isClarificationToolMessage(message: Message) {
  return message.type === "tool" && message.name === "ask_clarification";
}

export function extractPresentFilesFromMessage(message: Message) {
  if (!isAiMessage(message) || !hasPresentFiles(message)) {
    return [];
  }
  const files: string[] = [];
  for (const toolCall of getToolCalls(message)) {
    if (
      toolCall.name === "present_files" &&
      Array.isArray((toolCall.args as { filepaths?: string[] }).filepaths)
    ) {
      files.push(...((toolCall.args as { filepaths: string[] }).filepaths));
    }
  }
  return files;
}

/**
 * Extract timestamp from a message.
 * - Human messages: from additional_kwargs.timestamp (set by backend middleware)
 * - AI messages: from response_metadata.created_at (if provided by LLM)
 */
export function getMessageTimestamp(message: Message): string | null {
  if (message.type === "human") {
    const ts = message.additional_kwargs?.timestamp;
    if (typeof ts === "string") return ts;
  }
  if (message.type === "ai") {
    const createdAt = message.response_metadata?.created_at;
    if (typeof createdAt === "string") return createdAt;
    const ts = message.additional_kwargs?.timestamp;
    if (typeof ts === "string") return ts;
  }
  return null;
}

/**
 * Format an ISO timestamp to a locale datetime string (MM-DD HH:mm:ss).
 */
export function formatMessageTime(
  isoString: string | null | undefined,
): string {
  if (!isoString) return "";
  try {
    return new Date(isoString).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return "";
  }
}

export function hasSubagent(message: Message) {
  for (const toolCall of getToolCalls(message)) {
    if (toolCall.name === "task") {
      return true;
    }
  }
  return false;
}

export function findToolCallResult(toolCallId: string, messages: Message[]) {
  for (const message of messages) {
    if (message.type === "tool" && message.tool_call_id === toolCallId) {
      const content = extractTextFromMessage(message);
      if (content) {
        return content;
      }
    }
  }
  return undefined;
}

const VIEW_IMAGE_CONTEXT_MARKERS = [
  "Here are the images you've viewed:",
  "Here are the details of the images you've viewed:",
] as const;

function isViewImageContextMessage(message: Message): boolean {
  if (message.type !== "human") {
    return false;
  }
  if (message.additional_kwargs?.view_image_context === true) {
    return true;
  }
  const text = extractTextFromMessage(message);
  return VIEW_IMAGE_CONTEXT_MARKERS.some((marker) => text.includes(marker));
}

function isDynamicContextReminder(message: Message): boolean {
  return (
    message.type === "human" &&
    message.additional_kwargs?.dynamic_context_reminder === true
  );
}

function isDynamicContextUserCopy(message: Message): boolean {
  return (
    message.type === "human" &&
    typeof message.id === "string" &&
    message.id.endsWith("__user")
  );
}

// DynamicContextMiddleware splits the first human turn into a hidden reminder
// (original id) plus a visible copy (`{id}__user`). Later user turns append
// after the assistant reply, which can leave the first visible copy stranded
// below newer user messages in thread state.
export function repairDynamicContextUserMessageOrder(
  messages: Message[],
): Message[] {
  const result = [...messages];

  for (let index = 0; index < result.length; index += 1) {
    const userCopy = result[index];
    if (!userCopy || !isDynamicContextUserCopy(userCopy)) {
      continue;
    }

    const baseId = userCopy.id!.slice(0, -"__user".length);
    const reminderIndex = result.findIndex(
      (message) =>
        message.type === "human" &&
        message.id === baseId &&
        isDynamicContextReminder(message),
    );
    if (reminderIndex === -1) {
      continue;
    }

    const targetIndex = reminderIndex + 1;
    if (index === targetIndex) {
      continue;
    }

    result.splice(index, 1);
    result.splice(targetIndex, 0, userCopy);
    index = -1;
  }

  return result;
}

export function isHiddenFromUIMessage(message: Message) {
  return (
    message.additional_kwargs?.hide_from_ui === true ||
    message.additional_kwargs?.source === THREAD_SOURCE_SCHEDULED_TASK ||
    isViewImageContextMessage(message) ||
    (typeof message.name === "string" &&
      HIDDEN_CONTROL_MESSAGE_NAMES.has(message.name))
  );
}

/**
 * Represents a file stored in message additional_kwargs.files.
 * Used for optimistic UI (uploading state) and structured file metadata.
 */
export interface FileInMessage {
  filename: string;
  size: number; // bytes
  path?: string; // virtual path, may not be set during upload
  status?: "uploading" | "uploaded";
}

/**
 * Strip backend-injected context tags from a human message's content
 * before it's rendered to the user.
 *
 * These blocks are produced by LangGraph middlewares and addressed at the
 * model — they shouldn't leak into the chat UI:
 *
 * - ``<uploaded_files>...</uploaded_files>`` from :class:`UploadsMiddleware`
 * - ``<referenced_files>...</referenced_files>`` from
 *   :class:`ReferencedFilesMiddleware` (chat `@`-mention picker content)
 *
 * The strip is intentionally narrow to avoid eating user-typed markup
 * like ``<memory>`` (a user might legitimately ask the model about its
 * own memory system). For a defence-in-depth sweep that covers every
 * known backend marker — used by the export pipeline — see
 * {@link stripInternalMarkers}.
 */
export function stripUploadedFilesTag(content: string): string {
  return content
    .replace(/<uploaded_files>[\s\S]*?<\/uploaded_files>/g, "")
    .replace(/<referenced_files>[\s\S]*?<\/referenced_files>/g, "")
    .trim();
}

/**
 * Tag names that backend middlewares wrap around internal payloads before
 * letting them ride along inside LangGraph message ``content``.
 *
 * These markers are *not* user copy — they come from:
 *
 * - ``UploadsMiddleware`` → ``<uploaded_files>``
 * - ``ReferencedFilesMiddleware`` → ``<referenced_files>`` (chat `@` picker)
 * - ``DynamicContextMiddleware`` → ``<system-reminder>`` (carrying
 *   ``<memory>`` / ``<current_date>`` inside)
 * - ``TodoListMiddleware`` / ``LoopDetectionMiddleware`` style reminders
 *   live in ``hide_from_ui`` HumanMessages, but their inner payload uses
 *   the same tag vocabulary.
 *
 * The primary export filter is {@link isHiddenFromUIMessage}. This list is
 * the defence-in-depth strip for any message that — by middleware bug,
 * provider quirk, or merge-conflict regression — slips through without
 * its ``hide_from_ui`` flag set.
 */
export const INTERNAL_MARKER_TAGS = [
  "uploaded_files",
  "referenced_files",
  "system-reminder",
  "memory",
  "current_date",
] as const;

const INTERNAL_MARKER_RE = new RegExp(
  `<(${INTERNAL_MARKER_TAGS.join("|")})>[\\s\\S]*?</\\1>`,
  "g",
);

/**
 * Strip every known backend-injected marker from message content.
 *
 * Intended for the chat export path where a marker leaking through is a
 * privacy regression. UI render paths should keep using
 * {@link stripUploadedFilesTag} — they receive ``hide_from_ui`` messages
 * via a separate filter and the narrower function avoids stripping content
 * a user might legitimately type into a meta-discussion (e.g. asking the
 * model about its own ``<memory>`` system).
 */
export function stripInternalMarkers(content: string): string {
  return content.replace(INTERNAL_MARKER_RE, "").trim();
}

export function parseUploadedFiles(content: string): FileInMessage[] {
  // Match <uploaded_files>...</uploaded_files> tag
  const uploadedFilesRegex = /<uploaded_files>([\s\S]*?)<\/uploaded_files>/;
  // eslint-disable-next-line @typescript-eslint/prefer-regexp-exec
  const match = content.match(uploadedFilesRegex);

  if (!match) {
    return [];
  }

  const uploadedFilesContent = match[1];

  // Check if it's "No files have been uploaded yet."
  if (uploadedFilesContent?.includes("No files have been uploaded yet.")) {
    return [];
  }

  // Check if the backend reported no new files were uploaded in this message
  if (uploadedFilesContent?.includes("(empty)")) {
    return [];
  }

  // Parse file list
  // Format: - filename (size)\n  Path: /path/to/file
  const fileRegex = /- ([^\n(]+)\s*\(([^)]+)\)\s*\n\s*Path:\s*([^\n]+)/g;
  const files: FileInMessage[] = [];
  let fileMatch;

  while ((fileMatch = fileRegex.exec(uploadedFilesContent ?? "")) !== null) {
    files.push({
      filename: fileMatch[1].trim(),
      size: parseInt(fileMatch[2].trim(), 10) ?? 0,
      path: fileMatch[3].trim(),
    });
  }

  return files;
}

/**
 * Build a user-facing display name from a tool_call entry.
 *
 * For normal tools we use the tool name. For the internal ``task`` subagent
 * tool we show the human description when available, falling back to the
 * subagent type, so users see what each call actually did.
 */
function getToolCallDisplayName(
  toolCall: { name?: string; args?: Record<string, unknown> },
): string | null {
  const rawName = toolCall?.name;
  if (typeof rawName !== "string" || !rawName) return null;
  if (rawName !== "task") return rawName;
  const args = toolCall.args ?? {};
  const description =
    typeof args.description === "string" ? args.description.trim() : "";
  const subagentType =
    typeof args.subagent_type === "string" ? args.subagent_type.trim() : "";
  return description || subagentType || rawName;
}

/**
 * Detect and strip tool-call-omission markers from message content.
 *
 * The backend replaces redacted tool-call content with ``[工具调用已省略]``
 * or ``[工具调用: name1, name2]`` so that the model output still has
 * the right number of message turns.
 * In the UI we surface these as a collapsed banner instead of a
 * flood of raw markers.
 */
export const TOOL_OMISSION_MARKER = "[工具调用已省略]";
export const TOOL_OMISSION_NAMED_REGEX = /\[工具调用:\s*([^\]]+)\]/g;

export interface ToolOmissionInfo {
  count: number;
  toolNames: string[][];
  cleaned: string;
}

/**
 * Extract tool names directly from one or more LangChain Message objects.
 *
 * Each individual tool_call → one entry in the returned array (1:1 mapping
 * with the banner badges).  Skips the internal ``task`` tool used by the
 * subagent framework because users never see it as a standalone tool call.
 */
function _extractToolNamesFromMessages(
  messages: Message | Message[] | undefined | null,
): string[][] {
  if (!messages) return [];
  const list = Array.isArray(messages) ? messages : [messages];
  const result: string[][] = [];
  for (const msg of list) {
    if (!msg) continue;
    // Accept all AI-like message types (langgraph may use "ai", "AIMessage", or "AIMessageChunk")
    if (!isAiMessage(msg)) continue;
    // Prefer fully merged tool_calls array first (post-stream aggregation).
    const toolCalls = getToolCalls(msg);
    for (const tc of toolCalls) {
      const displayName = getToolCallDisplayName(tc);
      if (displayName) {
        result.push([displayName]);
      }
    }
    // Fall back to tool_call_chunks for mid-stream chunks that may not
    // have a fully merged tool_calls array yet.
    if (toolCalls.length === 0) {
      const chunks = (msg as unknown as { tool_call_chunks?: Array<{ name?: string; args?: Record<string, unknown> }> }).tool_call_chunks ?? [];
      for (const chunk of chunks) {
        const displayName = getToolCallDisplayName(chunk);
        if (displayName) {
          result.push([displayName]);
        }
      }
    }
  }
  return result;
}

export function detectToolOmissions(
  content: string,
  messages?: Message | Message[] | null,
): ToolOmissionInfo {
  if (!content && !messages) return { count: 0, toolNames: [], cleaned: content ?? "" };

  const toolNamesList: string[][] = [];
  let cleaned = (content ?? "").toString();

  // First extract named markers: [工具调用: tool1, tool2]
  cleaned = cleaned.replace(TOOL_OMISSION_NAMED_REGEX, (_match, namesStr: string) => {
    const names = namesStr
      .split(",")
      .map((n: string) => n.trim())
      .filter(Boolean);
    toolNamesList.push(names);
    return "";
  });

  // Then count unnamed markers: [工具调用已省略]
  const escaped = TOOL_OMISSION_MARKER.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const unnamedRegex = new RegExp(escaped, "g");
  const unnamedMatches = cleaned.match(unnamedRegex) || [];
  const unnamedCount = unnamedMatches.length;
  for (let i = 0; i < unnamedCount; i++) {
    toolNamesList.push([]);
  }
  cleaned = cleaned.replace(unnamedRegex, "");

  // Back-fill unnamed markers with tool names from Message.tool_calls.
  // Each tool_call → one badge entry (1:1 with the #N badges shown).
  const msgToolNames = _extractToolNamesFromMessages(messages);
  if (msgToolNames.length > 0) {
    // Back-fill empty entries first (markers that had no names).
    let msgIdx = 0;
    for (let i = 0; i < toolNamesList.length && msgIdx < msgToolNames.length; i++) {
      if (toolNamesList[i]!.length === 0) {
        const val = msgToolNames[msgIdx];
        if (val) {
          toolNamesList[i] = val;
        }
        msgIdx += 1;
      }
    }
    // Also back-fill NAMED entries that only had placeholder names from
    // [工具调用: xxx] markers — the real tool_calls array is authoritative.
    for (let i = 0; i < toolNamesList.length && msgIdx < msgToolNames.length; i++) {
      const val = msgToolNames[msgIdx];
      if (val) {
        toolNamesList[i] = val;
      }
      msgIdx += 1;
    }
    // If there are more individual tool calls than markers (e.g. the
    // content was fully cleaned but the message object still carries calls),
    // append the remaining names so the banner count stays accurate.
    while (msgIdx < msgToolNames.length) {
      const val = msgToolNames[msgIdx];
      if (val) {
        toolNamesList.push(val);
      }
      msgIdx += 1;
    }
  }

  const count = toolNamesList.length;
  return { count, toolNames: toolNamesList, cleaned };
}

/**
 * Classification for a friendly LLM error.
 *
 * - `none`     — no error pattern was detected; output as normal content.
 * - `known`    — a specific known error (rate-limit, auth, context-length,
 *                etc.) was detected; render with a mild warning style.
 * - `fallback` — only the generic "模型处理请求时发生了错误" catch-all
 *                matched; render with a stronger warning style and allow the
 *                user to expand & inspect the raw original content.
 */
export type FriendlyAiErrorTier = "none" | "known" | "fallback";

export interface FriendlyAiErrorResult {
  message: string;
  tier: FriendlyAiErrorTier;
  /** Original text when the function replaced it; useful for the details view. */
  original?: string;
}

/**
 * Detect and translate raw provider errors that leaked into an AI message's
 * content into a user-friendly Chinese sentence.  This is the frontend
 * second-line-of-defense; the backend already maps common provider errors
 * via ``_friendly_error_message``, but some paths may still write the
 * provider's raw dict/text before the error can be intercepted.
 *
 * Returns a structured result: the friendly message, a classification tier
 * the UI can use for styling, and the original raw snippet if the message
 * was rewritten so users can expand and inspect it when debugging.
 */
export function friendlyAiErrorMessage(content: unknown): FriendlyAiErrorResult {
  const empty: FriendlyAiErrorResult = { message: "", tier: "none" };
  if (typeof content !== "string" || !content) return empty;

  // --- Raw-error preservation sentinel --------------------------------------
  // When the backend patches a checkpoint after an LLM failure, it keeps the
  // original provider error inside an HTML comment like this:
  //   Friendly line\n<!--DF_RAW_ERROR:BodyOfTheProviderError-->
  // Peel it off BEFORE we do pattern matching so the banner shows the clean
  // friendly sentence, while the details expander still exposes the true
  // provider response (indispensable when the friendly tier misclassifies
  // the real error, e.g. a non-empty-text 400 being mistaken for empty).
  const RAW_RE =
    /\n<!--DF_RAW_ERROR:([\s\S]*?)-->\s*$/;
  const rawMatch = content.match(RAW_RE);
  const strippedContent = rawMatch ? content.replace(RAW_RE, "") : content;
  const rawSnippet = rawMatch
    ? rawMatch[1].replace(/-->\\?>/g, "-->").trim()
    : undefined;

  // Canonical working copy for all string checks below.
  const working = strippedContent;
  const low = working.toLowerCase();

  const asKnown = (message: string): FriendlyAiErrorResult => ({
    message,
    tier: "known",
    // Prefer the backend-attached true error when available; otherwise keep
    // the incoming text so the expander still has something meaningful.
    original: rawSnippet ?? content,
  });
  const asFallback = (message: string): FriendlyAiErrorResult => ({
    message,
    tier: "fallback",
    original: rawSnippet ?? content,
  });

  // --- Known error patterns -------------------------------------------------
  // Note: specific patterns are checked BEFORE the generic "llm request failed"
  // catch-all at the bottom so that concrete types get a precise message.
  if (
    low.includes("concurrent request limit") ||
    low.includes("access_terminated_error") ||
    (low.includes("error code: 403") &&
      (low.includes("concurrent") || low.includes("ongoing")))
  ) {
    return asKnown("当前同时运行的请求太多了，请稍等几秒，等之前的请求处理完后再试试。");
  }
  if (low.includes("403 并发") || low.includes("并发数超限") || low.includes("并发请求") || low.includes("同时运行的请求太多")) {
    // Backend already rewrote into the same friendly sentence we'd return here
    return asKnown(working);
  }
  // "服务暂时繁忙" / "服务繁忙" — backend-friendly rewrite for Kimi 403 / concurrency
  if (
    working.includes("服务暂时繁忙") ||
    working.includes("服务繁忙") ||
    working.includes("服务暂时不可用")
  ) {
    return asKnown(working);
  }
  if (
    low.includes("too many requests") ||
    low.includes("rate limit") ||
    low.includes("error code: 429") ||
    low.includes("error code: 403") ||
    /接口调用频率过高|并发数超限/.test(working)
  ) {
    // Accept the backend-friendly rewrite (matches Chinese) or raw provider string.
    if (/接口调用频率过高|并发数超限/.test(working)) return asKnown(working);
    return asKnown("接口调用频率过高或并发数超限，请稍等一会儿再试。");
  }
  if (
    low.includes("unauthorized") ||
    low.includes("invalid api key") ||
    low.includes("authentication") ||
    low.includes("error code: 401") ||
    working.includes("模型认证失败")
  ) {
    if (working.includes("模型认证失败")) return asKnown(working);
    return asKnown("模型认证失败，请联系管理员检查模型配置。");
  }
  if (
    low.includes("quota") ||
    low.includes("insufficient balance") ||
    low.includes("no balance") ||
    working.includes("配额或余额不足")
  ) {
    if (working.includes("配额或余额不足")) return asKnown(working);
    return asKnown("模型账户的配额或余额不足，请联系管理员充值或调整配额。");
  }
  if (
    low.includes("content policy") ||
    low.includes("safety") ||
    low.includes("rejected") ||
    working.includes("安全策略，已被拒绝")
  ) {
    if (working.includes("安全策略")) return asKnown(working);
    return asKnown("请求内容不符合模型的安全策略，已被拒绝。");
  }
  if (
    low.includes("context length") ||
    low.includes("maximum context") ||
    low.includes("max tokens") ||
    working.includes("对话内容太长")
  ) {
    if (working.includes("对话内容太长")) return asKnown(working);
    return asKnown("对话内容太长，请先清理一下对话历史或缩短输入内容后再试。");
  }
  if (
    low.includes("502") ||
    low.includes("503") ||
    low.includes("504") ||
    low.includes("bad gateway") ||
    low.includes("service unavailable") ||
    low.includes("500") ||
    working.includes("模型服务暂时不可用") ||
    working.includes("配置的模型不可用")
  ) {
    if (
      working.includes("模型服务暂时不可用") ||
      working.includes("配置的模型不可用")
    )
      return asKnown(working);
    if (
      /model not found|no such model|invalid model|model is not/.test(low)
    ) {
      return asKnown("当前配置的模型不可用，请联系管理员检查模型设置。");
    }
    return asKnown("模型服务暂时不可用，请稍后再试或联系管理员。");
  }
  if (
    low.includes("timeout") || low.includes("timed out") || working.includes("模型响应超时")
  ) {
    if (working.includes("模型响应超时")) return asKnown(working);
    return asKnown("模型响应超时，请稍后再试。");
  }
  if (
    (low.includes("connection") &&
      (low.includes("refused") || low.includes("reset"))) ||
    low.includes("econnrefused") ||
    working.includes("暂时无法连接到模型服务")
  ) {
    if (working.includes("暂时无法连接到模型服务")) return asKnown(working);
    return asKnown("暂时无法连接到模型服务，请稍后再试。");
  }
  // Empty user message (e.g. a pure-file send) — providers such as Kimi
  // reject it as 400 invalid_request_error with a 'text content is empty'
  // or 'message content cannot be empty' hint. Conditions below are
  // intentionally narrow — do not mistake OTHER invalid_request_errors
  // (e.g. bad request_id format, malformed schema, …) for a user-side
  // "empty text" mistake.  If unsure, fall through to the generic card
  // with the raw details so the user/administrator can see the real cause.
  if (
    low.includes("text content is empty") ||
    /text.*(?:content|prompt|message).*empty/i.test(working) ||
    /content.*cannot.*(?:be\s*)?empty|message.*cannot.*(?:be\s*)?empty/i.test(working) ||
    (low.includes("invalid_request_error") &&
      /(?:text|message|prompt|content).*empty|empty.*(?:text|message|prompt)/i.test(
        working,
      )) ||
    (low.includes("error code: 400") &&
      /text.*empty|content.*empty|prompt.*empty|message.*empty/i.test(working))
  ) {
    return asKnown("请求的文字内容为空，请补充文字说明后再发送（纯图片/附件需要附带描述）。");
  }

  // --- Generic ugly error signal: e.g. "LLM request failed: Error code: 403 - ..."
  const uglySignal =
    low.startsWith("llm request failed") ||
    low.startsWith("error code:") ||
    low.includes("{'error':") ||
    low.includes('"error": {');
  const genericFallbackText = "模型处理请求时发生了错误，请稍后再试。";
  if (uglySignal) {
    return asFallback(genericFallbackText);
  }
  // Content already equals the generic backend-friendly fallback (no raw signal visible)
  if (working.trim() === genericFallbackText) {
    return asFallback(working);
  }

  // If the backend attached a raw error snippet but no pattern matched the
  // clean face, still surface it as a *fallback* card.  It keeps the user's
  // already-friendly-looking sentence intact (so they still get readable UI)
  // AND exposes the raw error in the expander for investigation.
  if (rawSnippet) {
    return {
      message: working,
      tier: "fallback",
      original: rawSnippet,
    };
  }

  return { message: working, tier: "none" };
}
