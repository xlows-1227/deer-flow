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
